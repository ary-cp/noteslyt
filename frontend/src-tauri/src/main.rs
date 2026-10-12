#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs,
    fs::OpenOptions,
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, RunEvent};

struct BackendProcess(Mutex<Option<Child>>);

fn source_root() -> Result<PathBuf, String> {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|frontend| frontend.parent())
        .map(PathBuf::from)
        .ok_or_else(|| "Could not locate the NotesLyt repository root".to_string())
}

fn backend_responds(port: u16) -> bool {
    let address = match format!("127.0.0.1:{port}")
        .to_socket_addrs()
        .ok()
        .and_then(|mut a| a.next())
    {
        Some(address) => address,
        None => return false,
    };
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(250)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = String::new();
    stream.read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200")
}

fn writable_data_dir(path: &PathBuf) -> std::io::Result<()> {
    fs::create_dir_all(path)?;
    let probe = path.join(format!(".noteslyt-write-check-{}", std::process::id()));
    let file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&probe)?;
    drop(file);
    fs::remove_file(probe)
}

fn start_backend(app: &tauri::App) -> Result<Option<Child>, String> {
    let packaged = !cfg!(debug_assertions);
    let port: u16 = if packaged { 8765 } else { 8000 };
    if backend_responds(port) {
        return Ok(None);
    }

    let dev_root = source_root()?;
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| error.to_string())?;
    let migrations_dir = if packaged {
        resource_dir.join("migrations")
    } else {
        dev_root.join("migrations")
    };
    let per_user_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?
        .join("data");
    let data_dir = if packaged {
        let install_data_dir = std::env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(|parent| parent.join("data")));
        match install_data_dir {
            Some(path) if writable_data_dir(&path).is_ok() => path,
            _ => {
                writable_data_dir(&per_user_data_dir)
                    .map_err(|error| format!("Could not create NotesLyt data storage: {error}"))?;
                per_user_data_dir
            }
        }
    } else {
        dev_root.join("data")
    };
    let working_dir = if packaged {
        data_dir.clone()
    } else {
        dev_root.clone()
    };
    let frontend_dir = if packaged {
        resource_dir.join("frontend").join("dist")
    } else {
        dev_root.join("frontend").join("dist")
    };

    let mut command = if packaged {
        Command::new(
            resource_dir
                .join("backend-runtime")
                .join("noteslyt-api")
                .join("noteslyt-api.exe"),
        )
    } else {
        let mut uv = Command::new("uv");
        uv.arg("run")
            .arg("--project")
            .arg(&dev_root)
            .arg("--")
            .args(["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port"])
            .arg(port.to_string())
            .arg("--app-dir")
            .arg(&dev_root);
        uv
    };
    command
        .current_dir(&working_dir)
        .env("NOTESLYT_API_PORT", port.to_string())
        .env("FRONTEND_DIR", &frontend_dir)
        .env("MIGRATIONS_DIR", &migrations_dir)
        .env("SQLITE_MIGRATIONS_DIR", migrations_dir.join("sqlite"))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if packaged {
        command
            .env_remove("DATABASE_URL")
            .env("DATA_DIR", &data_dir);
    } else {
        command.env("UV_PROJECT_ENVIRONMENT", dev_root.join(".venv"));
    }
    let child = command.spawn().map_err(|error| {
        if packaged {
            format!("Could not start the bundled NotesLyt API: {error}")
        } else {
            format!("Could not start the NotesLyt API with uv: {error}. Install uv and sync the project environment first.")
        }
    })?;

    let mut child = child;
    let deadline = Instant::now() + Duration::from_secs(60);
    while Instant::now() < deadline {
        if backend_responds(port) {
            return Ok(Some(child));
        }
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            return Err(format!("The NotesLyt API stopped during startup ({status}). Check the app's data folder and model settings."));
        }
        thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
    Err("The NotesLyt API did not become ready within 60 seconds. Check the app's data folder and model settings.".to_string())
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let backend = start_backend(app).map_err(std::io::Error::other)?;
            app.manage(BackendProcess(Mutex::new(backend)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build NotesLyt desktop app");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(process) = app_handle.try_state::<BackendProcess>() {
                if let Ok(mut child) = process.0.lock() {
                    if let Some(child) = child.as_mut() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        }
    });
}

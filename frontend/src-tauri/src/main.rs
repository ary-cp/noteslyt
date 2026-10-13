#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs,
    fs::OpenOptions,
    io::{Read, Write},
    net::{TcpStream, ToSocketAddrs},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager, RunEvent};

struct BackendProcess {
    child: Mutex<Option<Child>>,
    exiting: AtomicBool,
}

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

/// OS-specific ffmpeg bundle subdir (matches `frontend/scripts/fetch-ffmpeg.mjs`).
fn ffmpeg_subdir() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows-x64"
    } else if cfg!(target_os = "macos") {
        if cfg!(target_arch = "aarch64") {
            "macos-arm64"
        } else {
            "macos-x64"
        }
    } else {
        "linux-x64"
    }
}

fn exe_name(base: &str) -> String {
    if cfg!(target_os = "windows") {
        format!("{base}.exe")
    } else {
        base.to_string()
    }
}

/// Locate the shipped ffmpeg/ffprobe pair. The bundle ALWAYS wins over any
/// system install: when these files exist the backend is pointed at them and
/// never falls back to PATH.
fn bundled_ffmpeg(ffmpeg_root: &PathBuf) -> (Option<PathBuf>, Option<PathBuf>) {
    let dir = ffmpeg_root.join(ffmpeg_subdir());
    // Older dev checkouts may have flattened binaries directly under the root.
    let candidates = [dir.clone(), ffmpeg_root.clone()];
    let mut ffmpeg = None;
    let mut ffprobe = None;
    for base in &candidates {
        let f = base.join(exe_name("ffmpeg"));
        if f.is_file() && ffmpeg.is_none() {
            ffmpeg = Some(f);
        }
        let p = base.join(exe_name("ffprobe"));
        if p.is_file() && ffprobe.is_none() {
            ffprobe = Some(p);
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        for exe in ffmpeg.iter().chain(ffprobe.iter()) {
            if let Ok(meta) = fs::metadata(exe) {
                let mut perms = meta.permissions();
                if perms.mode() & 0o111 == 0 {
                    perms.set_mode(0o755);
                    let _ = fs::set_permissions(exe, perms);
                }
            }
        }
    }
    (ffmpeg, ffprobe)
}

fn prepend_to_path(dir: &PathBuf) {
    let mut paths = vec![dir.clone()];
    if let Some(existing) = std::env::var_os("PATH") {
        paths.extend(std::env::split_paths(&existing));
    }
    if let Ok(joined) = std::env::join_paths(paths) {
        std::env::set_var("PATH", &joined);
    }
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

/// Stop the backend and every child process it started (including llama-server).
/// Killing only the FastAPI process can orphan llama.cpp and leave its VRAM in use.
fn kill_process_tree(child: &mut Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        let pid = child.id().to_string();
        let result = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .status();
        if result.is_ok_and(|status| status.success()) {
            let _ = child.wait();
            return;
        }
    }

    let _ = child.kill();
    let _ = child.wait();
}

fn start_backend(app: &AppHandle) -> Result<Option<Child>, String> {
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

    // Bundled ffmpeg: the installer ships every OS build under ffmpeg/;
    // the backend is pointed at this machine's subdir and the bundle
    // ALWAYS wins over any system install (no PATH fallback).
    let ffmpeg_root = if packaged {
        resource_dir.join("ffmpeg")
    } else {
        dev_root
            .join("frontend")
            .join("src-tauri")
            .join("binaries-ffmpeg")
    };
    let (bundled_ffmpeg_exe, bundled_ffprobe_exe) = bundled_ffmpeg(&ffmpeg_root);
    if let Some(exe) = bundled_ffmpeg_exe.as_ref().and_then(|p| p.parent()) {
        prepend_to_path(&exe.to_path_buf());
    }
    if bundled_ffmpeg_exe.is_none() {
        eprintln!(
            "NotesLyt: no bundled ffmpeg found under {} — media ingestion falls back to system PATH. Run `npm run ffmpeg:fetch` in frontend/.",
            ffmpeg_root.display()
        );
    }

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
        .env("NOTESLYT_RESOURCE_DIR", &resource_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    if let Some(exe) = bundled_ffmpeg_exe.as_ref() {
        command.env("FFMPEG_PATH", exe);
    }
    if let Some(exe) = bundled_ffprobe_exe.as_ref() {
        command.env("FFPROBE_PATH", exe);
    }
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
    kill_process_tree(&mut child);
    Err("The NotesLyt API did not become ready within 60 seconds. Check the app's data folder and model settings.".to_string())
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            app.manage(BackendProcess {
                child: Mutex::new(None),
                exiting: AtomicBool::new(false),
            });
            let handle = app.handle().clone();
            thread::spawn(move || match start_backend(&handle) {
                Ok(child) => {
                    if let Some(process) = handle.try_state::<BackendProcess>() {
                        if process.exiting.load(Ordering::SeqCst) {
                            if let Some(mut child) = child {
                                kill_process_tree(&mut child);
                            }
                            return;
                        }
                        if let Ok(mut running) = process.child.lock() {
                            *running = child;
                        }
                    }
                    if !cfg!(debug_assertions) {
                        if let Some(window) = handle.get_webview_window("main") {
                            if let Ok(url) = tauri::Url::parse("http://127.0.0.1:8765/") {
                                let _ = window.navigate(url);
                            }
                        }
                    }
                }
                Err(error) => {
                    eprintln!("NotesLyt backend startup failed: {error}");
                    if !cfg!(debug_assertions) {
                        if let Some(window) = handle.get_webview_window("main") {
                            let _ = window.eval("document.body.classList.add('startup-error')");
                        }
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build NotesLyt desktop app");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(process) = app_handle.try_state::<BackendProcess>() {
                process.exiting.store(true, Ordering::SeqCst);
                if let Ok(mut child) = process.child.lock() {
                    if let Some(child) = child.as_mut() {
                        kill_process_tree(child);
                    }
                }
            }
        }
    });
}

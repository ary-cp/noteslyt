/** Runtime split: browsers are the hosted website; Tauri is the local desktop app. */
export const isDesktopApp =
  typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const runtimeMode = isDesktopApp ? "app" : "website";

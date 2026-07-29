use tauri::Manager;

#[tauri::command]
fn pixel_manager_api_key() -> Result<String, String> {
    let key = option_env!("PIXEL_MANAGER_API_KEY").unwrap_or("").trim();
    if key.is_empty() {
        return Err("账号池管理密钥未配置".to_string());
    }
    Ok(key.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![pixel_manager_api_key])
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .run(tauri::generate_context!())
        .expect("error while running 91");
}

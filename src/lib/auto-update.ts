type AutoUpdateEvent =
  | { type: "downloading"; message: string }
  | { type: "ready"; message: string; relaunch: () => Promise<void> }
  | { type: "error"; message: string };

const isTauriRuntime = "__TAURI_INTERNALS__" in globalThis || "__TAURI__" in globalThis;

let updateCheckStarted = false;

export async function installAvailableUpdate(onEvent: (event: AutoUpdateEvent) => void): Promise<void> {
  if (!isTauriRuntime || updateCheckStarted) return;
  updateCheckStarted = true;
  let updateFound = false;

  try {
    const [{ check }, { relaunch }] = await Promise.all([
      import("@tauri-apps/plugin-updater"),
      import("@tauri-apps/plugin-process"),
    ]);
    const update = await check({ timeout: 15_000 });
    if (!update) return;

    updateFound = true;
    onEvent({ type: "downloading", message: `发现新版 ${update.version}，正在下载更新` });
    await update.downloadAndInstall();
    onEvent({
      type: "ready",
      message: `新版 ${update.version} 已安装，重启后生效`,
      relaunch,
    });
  } catch (error) {
    if (updateFound) {
      onEvent({
        type: "error",
        message: error instanceof Error ? `自动更新失败：${error.message}` : "自动更新失败",
      });
    }
  }
}

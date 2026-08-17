type AutoUpdateEvent =
  | { type: "checking"; message: string }
  | { type: "current"; message: string }
  | { type: "downloading"; message: string }
  | { type: "ready"; message: string; relaunch: () => Promise<void> }
  | { type: "error"; message: string };

const isTauriRuntime = "__TAURI_INTERNALS__" in globalThis || "__TAURI__" in globalThis;

let automaticCheckStarted = false;
let updateCheckInFlight = false;
let installedUpdate: { message: string; relaunch: () => Promise<void> } | null = null;

async function checkAndInstallUpdate(
  onEvent: (event: AutoUpdateEvent) => void,
  options: { manual: boolean },
): Promise<void> {
  if (!isTauriRuntime) return;
  if (installedUpdate) {
    onEvent({ type: "ready", ...installedUpdate });
    return;
  }
  if (updateCheckInFlight) {
    if (options.manual) onEvent({ type: "checking", message: "正在检查更新" });
    return;
  }

  updateCheckInFlight = true;
  let updateFound = false;

  try {
    if (options.manual) onEvent({ type: "checking", message: "正在检查更新" });
    const [{ check }, { relaunch }] = await Promise.all([
      import("@tauri-apps/plugin-updater"),
      import("@tauri-apps/plugin-process"),
    ]);
    const update = await check({ timeout: 15_000 });
    if (!update && options.manual) {
      onEvent({ type: "current", message: "当前已是最新版本" });
    }
    if (!update) return;

    updateFound = true;
    onEvent({ type: "downloading", message: `发现新版 ${update.version}，正在下载更新` });
    await update.downloadAndInstall();
    installedUpdate = { message: `新版 ${update.version} 已安装，重启后生效`, relaunch };
    onEvent({ type: "ready", ...installedUpdate });
  } catch (error) {
    if (updateFound || options.manual) {
      onEvent({
        type: "error",
        message: error instanceof Error ? `自动更新失败：${error.message}` : "自动更新失败",
      });
    }
  } finally {
    updateCheckInFlight = false;
  }
}

export async function installAvailableUpdate(onEvent: (event: AutoUpdateEvent) => void): Promise<void> {
  if (automaticCheckStarted) return;
  automaticCheckStarted = true;
  await checkAndInstallUpdate(onEvent, { manual: false });
}

export async function checkForUpdatesNow(onEvent: (event: AutoUpdateEvent) => void): Promise<void> {
  await checkAndInstallUpdate(onEvent, { manual: true });
}

export async function installUpdateMenu(onEvent: (event: AutoUpdateEvent) => void): Promise<void> {
  if (!isTauriRuntime) return;
  const [{ Menu, MenuItem, PredefinedMenuItem }] = await Promise.all([
    import("@tauri-apps/api/menu"),
  ]);
  const menu = await Menu.default();
  const topItems = await menu.items();
  const appMenu = topItems.find((item) => item.kind === "Submenu");
  if (!appMenu || !("insert" in appMenu)) return;
  if (await appMenu.get("check-updates")) {
    await menu.setAsAppMenu();
    return;
  }

  const checkUpdates = await MenuItem.new({
    id: "check-updates",
    text: "检查更新...",
    action: () => void checkForUpdatesNow(onEvent),
  });
  const separator = await PredefinedMenuItem.new({ item: "Separator" });
  await appMenu.insert([checkUpdates, separator], 1);
  await menu.setAsAppMenu();
}

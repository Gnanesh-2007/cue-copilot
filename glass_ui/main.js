const { app, BrowserWindow, globalShortcut, ipcMain, dialog, screen } = require('electron');
const path = require('path');

let win = null;
let clickThrough = false;
let isVisible = true;
let expandedHeight = 460;
let expandedWidth = 780;
const PILL_HEIGHT = 54;
const MINI_WIDTH = 250;
const MINI_HEIGHT = 48;

const WS_PORT = process.env.CUE_WS_PORT || '8765';

function createWindow() {
  const { width: scrW } = screen.getPrimaryDisplay().workAreaSize;
  const initW = 780;
  const initX = Math.round((scrW - initW) / 2);

  win = new BrowserWindow({
    width: initW,
    height: expandedHeight,
    x: initX,
    y: 24,
    minWidth: 520,
    minHeight: PILL_HEIGHT,
    frame: false,
    type: 'toolbar',
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    resizable: true,
    focusable: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // Always float above fullscreen / meeting windows & stay off Taskbar + Alt-Tab
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setSkipTaskbar(true);

  // Stealth: invisible to Google Meet / Zoom / Teams screen sharing (WDA_EXCLUDEFROMCAPTURE)
  if (!process.env.CUE_NOSTEALTH) {
    win.setContentProtection(true);
  }

  // Real Windows 11 Acrylic frosted-glass backdrop blur
  try {
    win.setBackgroundMaterial('acrylic');
  } catch (e) {
    // Fallback to pure CSS glass on older Windows builds
  }

  win.loadFile(path.join(__dirname, 'index.html'), {
    query: { port: WS_PORT },
  });

  win.on('resize', () => {
    if (!win) return;
    const [w, h] = win.getSize();
    if (h > PILL_HEIGHT + 30) {
      expandedHeight = h;
      expandedWidth = w;
    }
  });

  win.on('closed', () => {
    win = null;
  });

  registerGlobalHotkeys();
}

function sendAction(action, payload = {}) {
  if (win && !win.isDestroyed()) {
    win.webContents.send('hotkey', { action, ...payload });
  }
}

function registerGlobalHotkeys() {
  const bind = (accel, fn) => {
    try {
      globalShortcut.register(accel, fn);
    } catch (e) {
      // Ignore if another app holds the key
    }
  };

  bind('F6', () => sendAction('toggle_auto'));
  bind('F7', () => sendAction('force_answer'));
  bind('F8', () => {
    if (!win) return;
    if (isVisible) {
      win.setOpacity(0);
      win.setIgnoreMouseEvents(true);
      isVisible = false;
    } else {
      win.setOpacity(clickThrough ? 0.78 : 1.0);
      win.setIgnoreMouseEvents(clickThrough, { forward: true });
      win.show();
      win.setAlwaysOnTop(true, 'screen-saver');
      if (!process.env.CUE_NOSTEALTH) win.setContentProtection(true);
      isVisible = true;
    }
  });
  bind('F9', () => sendAction('screen_ocr'));
  bind('F10', () => {
    if (!win) return;
    clickThrough = !clickThrough;
    win.setIgnoreMouseEvents(clickThrough, { forward: true });
    win.setOpacity(clickThrough ? 0.78 : 1.0);
    sendAction('click_through_state', { on: clickThrough });
  });
  bind('F11', () => sendAction('toggle_teleprompter'));
  bind('F12', () => sendAction('region_capture'));
  bind('Alt+Down', () => sendAction('scroll', { delta: 120 }));
  bind('Alt+Up', () => sendAction('scroll', { delta: -120 }));
}

// IPC from renderer
ipcMain.on('set-collapsed', (_, collapsed) => {
  if (!win) return;
  const [w] = win.getSize();
  win.setSize(w, collapsed ? PILL_HEIGHT : Math.max(expandedHeight, 360), true);
});

ipcMain.on('set-mini-pill', (_, mini) => {
  if (!win) return;
  if (mini) {
    win.setMinimumSize(MINI_WIDTH, MINI_HEIGHT);
    win.setSize(MINI_WIDTH, MINI_HEIGHT, false);
  } else {
    win.setMinimumSize(520, PILL_HEIGHT);
    win.setSize(Math.max(expandedWidth, 680), Math.max(expandedHeight, 360), false);
  }
});

ipcMain.on('fit-height', (_, desiredH) => {
  if (!win) return;
  const { height: scrH } = screen.getPrimaryDisplay().workAreaSize;
  const maxH = Math.round(scrH * 0.68);
  const clamped = Math.max(220, Math.min(maxH, Math.round(Number(desiredH) || 380)));
  const [w, curH] = win.getSize();
  if (Math.abs(clamped - curH) > 12) {
    expandedHeight = clamped;
    win.setSize(w, clamped, false);
  }
});

ipcMain.on('set-opacity', (_, alpha) => {
  if (!win) return;
  win.setOpacity(Math.max(0.45, Math.min(1.0, Number(alpha) || 1.0)));
});

ipcMain.on('hide-window', () => {
  if (!win) return;
  win.hide();
  isVisible = false;
});

ipcMain.on('close-app', () => {
  app.quit();
});

ipcMain.handle('pick-resume', async () => {
  if (!win) return '';
  const res = await dialog.showOpenDialog(win, {
    title: 'Select Resume / Project Report',
    filters: [
      { name: 'Documents', extensions: ['pdf', 'docx', 'txt', 'md'] },
      { name: 'All Files', extensions: ['*'] },
    ],
    properties: ['openFile'],
  });
  if (res.canceled || !res.filePaths.length) return '';
  return res.filePaths[0];
});

ipcMain.handle('pick-docs', async () => {
  if (!win) return [];
  const res = await dialog.showOpenDialog(win, {
    title: 'Upload PDFs / Notes / Code to Knowledge Base',
    filters: [
      { name: 'Knowledge Files', extensions: ['pdf', 'docx', 'txt', 'md', 'py', 'js', 'ts', 'java', 'cpp', 'sql'] },
      { name: 'All Files', extensions: ['*'] },
    ],
    properties: ['openFile', 'multiSelections'],
  });
  if (res.canceled || !res.filePaths.length) return [];
  return res.filePaths;
});

app.whenReady().then(createWindow);

app.on('will-quit', () => {
  globalShortcut.unregisterAll();
});

app.on('window-all-closed', () => {
  app.quit();
});

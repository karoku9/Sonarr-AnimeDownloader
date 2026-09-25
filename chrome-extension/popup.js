const serverInput = document.getElementById("server-url");
const statusNode = document.getElementById("status");

function setStatus(message, error = false) {
  statusNode.textContent = message;
  statusNode.className = error ? "error" : "";
}

function normalizedServer() {
  return (serverInput.value.trim() || "http://localhost:5000").replace(/\/+$/, "");
}

async function grantServerPermission(serverUrl) {
  const origin = new URL(serverUrl).origin + "/*";
  if (origin.startsWith("http://localhost/") || origin.startsWith("http://127.0.0.1/")) return true;
  return chrome.permissions.request({origins: [origin]});
}

chrome.storage.sync.get({serverUrl: "http://localhost:5000"}, items => {
  serverInput.value = items.serverUrl;
});

document.getElementById("save").addEventListener("click", async () => {
  try {
    const serverUrl = normalizedServer();
    if (!await grantServerPermission(serverUrl)) return setStatus("Permission was not granted.", true);
    await chrome.storage.sync.set({serverUrl});
    setStatus("Saved. Reload an AnimeWorld page to open the panel.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

document.getElementById("check").addEventListener("click", async () => {
  try {
    const serverUrl = normalizedServer();
    if (!await grantServerPermission(serverUrl)) return setStatus("Permission was not granted.", true);
    const response = await fetch(`${serverUrl}/api/extension/status`);
    const payload = await response.json();
    if (!response.ok || payload.error) throw new Error(payload.error || "Connection failed.");
    setStatus(`${payload.data.version} connected. Default: ${payload.data.global_language_preference}.`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

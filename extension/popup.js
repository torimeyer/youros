const toggle = document.getElementById('toggle');
const status = document.getElementById('status');

chrome.storage.local.get(['captureEnabled'], (result) => {
  const on = !!result.captureEnabled;
  toggle.checked = on;
  setStatus(on);
});

toggle.addEventListener('change', () => {
  const on = toggle.checked;
  chrome.storage.local.set({ captureEnabled: on });
  setStatus(on);
});

function setStatus(on) {
  if (on) {
    status.textContent = 'Capture is ON';
    status.className = 'status on';
  } else {
    status.textContent = 'Capture is OFF — click to enable';
    status.className = 'status off';
  }
}

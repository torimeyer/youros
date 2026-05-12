const backendUrlInput = document.getElementById('backendUrl');
const authTokenInput = document.getElementById('authToken');
const saveBtn = document.getElementById('save');
const savedMsg = document.getElementById('saved');

chrome.storage.local.get(['backendUrl', 'authToken'], (result) => {
  backendUrlInput.value = result.backendUrl || 'http://127.0.0.1:8000';
  authTokenInput.value = result.authToken || '';
});

saveBtn.addEventListener('click', () => {
  chrome.storage.local.set({
    backendUrl: backendUrlInput.value.trim() || 'http://127.0.0.1:8000',
    authToken: authTokenInput.value.trim(),
  }, () => {
    savedMsg.style.display = 'inline';
    setTimeout(() => { savedMsg.style.display = 'none'; }, 2000);
  });
});

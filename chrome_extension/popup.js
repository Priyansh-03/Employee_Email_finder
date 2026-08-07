const firstNameInput = document.getElementById('firstName');
const lastNameInput = document.getElementById('lastName');
const companyInput = document.getElementById('company');
const resultDiv = document.getElementById('result');
const loadingDiv = document.getElementById('loading');
const animName = document.getElementById('animName');
const animStatus = document.getElementById('animStatus');
const btn = document.getElementById('findBtn');
const stopBtn = document.getElementById('stopBtn');

function statusFromLog(logs) {
  if (!logs || logs.length === 0) return 'Connecting...';
  const last = (logs[logs.length - 1].text || '').toLowerCase();
  if (last.includes('connect')) return 'Connecting to server...';
  if (last.includes('dns') || last.includes('mx') || last.includes('domain')) return 'Checking domain records...';
  if (last.includes('pattern') || last.includes('generat')) return 'Generating email patterns...';
  if (last.includes('brute') || last.includes('trying') || last.includes('attempt') || last.includes('combination')) return 'Trying email combinations...';
  if (last.includes('verif')) return 'Verifying email addresses...';
  if (last.includes('found') || last.includes('success')) return 'Almost there...';
  if (last.includes('error') || last.includes('fail')) return 'Retrying with alternate methods...';
  return 'Processing...';
}

function renderState(state) {
  if (!firstNameInput.value && state.firstName) firstNameInput.value = state.firstName;
  if (!lastNameInput.value && state.lastName) lastNameInput.value = state.lastName;
  if (!companyInput.value && state.company) companyInput.value = state.company;

  if (state.status === 'loading') {
    resultDiv.style.display = 'none';
    loadingDiv.style.display = 'block';
    animName.textContent = `${state.firstName} ${state.lastName}`;
    const newStatus = statusFromLog(state.logs);
    if (animStatus.textContent !== newStatus) {
      animStatus.style.animation = 'none';
      animStatus.offsetHeight; // reflow to restart animation
      animStatus.style.animation = '';
      animStatus.textContent = newStatus;
    }
    btn.style.display = 'none';
    stopBtn.style.display = 'block';
  } else if (state.status === 'done') {
    loadingDiv.style.display = 'none';
    resultDiv.style.display = 'block';
    resultDiv.innerHTML = state.resultHtml;
    resultDiv.className = state.resultClass;
    btn.style.display = 'block';
    stopBtn.style.display = 'none';
    btn.disabled = false;
    btn.textContent = 'Find Email';
  } else {
    loadingDiv.style.display = 'none';
    resultDiv.style.display = 'none';
    btn.style.display = 'block';
    stopBtn.style.display = 'none';
    btn.disabled = false;
    btn.textContent = 'Find Email';
  }
}

chrome.runtime.sendMessage({ action: 'getState' }, (state) => {
  if (state) {
    renderState(state);
  }
});

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'stateUpdated') {
    renderState(request.state);
  }
});

function saveInputs() {
  chrome.runtime.sendMessage({
    action: 'updateInputs',
    firstName: firstNameInput.value.trim(),
    lastName: lastNameInput.value.trim(),
    company: companyInput.value.trim()
  });
}

firstNameInput.addEventListener('input', saveInputs);
lastNameInput.addEventListener('input', saveInputs);
companyInput.addEventListener('input', saveInputs);

btn.addEventListener('click', () => {
  const firstName = firstNameInput.value.trim();
  const lastName = lastNameInput.value.trim();
  const company = companyInput.value.trim();
  
  if (!firstName || !lastName || !company) {
    resultDiv.textContent = 'Please fill in all fields.';
    resultDiv.className = 'error';
    resultDiv.style.display = 'block';
    return;
  }

  chrome.runtime.sendMessage({
    action: 'findEmail',
    firstName: firstName,
    lastName: lastName,
    company: company
  });
  
  renderState({
    status: 'loading',
    firstName: firstName,
    lastName: lastName,
    company: company,
    logs: [{ text: 'Connecting to server...', class: 'log-entry' }]
  });
});

stopBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'stopSearch' });
});
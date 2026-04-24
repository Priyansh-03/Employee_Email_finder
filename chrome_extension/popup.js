const firstNameInput = document.getElementById('firstName');
const lastNameInput = document.getElementById('lastName');
const companyInput = document.getElementById('company');
const resultDiv = document.getElementById('result');
const loadingDiv = document.getElementById('loading');
const loadingHeader = document.getElementById('loadingHeader');
const progressLog = document.getElementById('progressLog');
const btn = document.getElementById('findBtn');
const stopBtn = document.getElementById('stopBtn');

function renderState(state) {
  if (!firstNameInput.value && state.firstName) firstNameInput.value = state.firstName;
  if (!lastNameInput.value && state.lastName) lastNameInput.value = state.lastName;
  if (!companyInput.value && state.company) companyInput.value = state.company;

  if (state.status === 'loading') {
    resultDiv.style.display = 'none';
    loadingDiv.style.display = 'block';
    loadingHeader.innerHTML = state.loadingHeader || `<strong>Finding email for:</strong><br><span style="color:#007bff; font-size:16px; font-weight:bold;">${state.firstName} ${state.lastName}</span>`;
    
    progressLog.innerHTML = state.logs.map(log => `<div class="${log.class}">${log.text}</div>`).join('');
    progressLog.scrollTop = progressLog.scrollHeight; // Auto-scroll to bottom
    
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
    loadingHeader: `<strong>Finding email for:</strong><br><span style="color:#007bff; font-size:16px; font-weight:bold;">${firstName} ${lastName}</span>`,
    logs: [{ text: 'Connecting to server...', class: 'log-entry' }]
  });
});

stopBtn.addEventListener('click', () => {
  chrome.runtime.sendMessage({ action: 'stopSearch' });
});
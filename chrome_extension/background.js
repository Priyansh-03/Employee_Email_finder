let appState = {
  status: 'idle', // idle, loading, done
  firstName: '',
  lastName: '',
  company: '',
  resultHtml: '',
  resultClass: '',
  loadingHeader: '',
  logs: [],
  sessionId: ''
};

// Load state from storage on startup
chrome.storage.local.get(['appState'], (result) => {
  if (result.appState) {
    appState = result.appState;
    // If it was loading when the service worker went to sleep, reset it
    if (appState.status === 'loading') {
      appState.status = 'idle';
    }
  }
});

function saveState() {
  chrome.storage.local.set({ appState: appState });
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === 'getState') {
    sendResponse(appState);
  } else if (request.action === 'updateInputs') {
    appState.firstName = request.firstName;
    appState.lastName = request.lastName;
    appState.company = request.company;
    
    if (appState.status === 'done') {
        appState.status = 'idle'; 
        appState.resultHtml = '';
        appState.resultClass = '';
        appState.loadingHeader = '';
        appState.logs = [];
    }
    
    saveState();
    sendResponse({ success: true });
  } else if (request.action === 'findEmail') {
    appState.status = 'loading';
    appState.firstName = request.firstName;
    appState.lastName = request.lastName;
    appState.company = request.company;
    appState.resultHtml = '';
    appState.resultClass = '';
    appState.logs = [];
    appState.sessionId = Date.now().toString() + Math.random().toString(36).substr(2, 9);
    appState.loadingHeader = `<strong>Finding email for:</strong><br><span style="color:#007bff; font-size:16px; font-weight:bold;">${appState.firstName} ${appState.lastName}</span>`;
    saveState();
    
    chrome.runtime.sendMessage({ action: 'stateUpdated', state: appState }).catch(() => {});

    const url = new URL('http://127.0.0.1:5000/stream_find_email');
    url.searchParams.append('first_name', request.firstName);
    url.searchParams.append('last_name', request.lastName);
    url.searchParams.append('company', request.company);
    url.searchParams.append('session_id', appState.sessionId);

    const eventSource = new EventSource(url.toString());

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'progress') {
        let rawMsg = data.message || '';
        
        if (data.current_email) {
          appState.loadingHeader = `<strong>Testing:</strong><br><span style="color:#007bff; font-size:15px;">${data.current_email}</span>`;
        }
        
        if (rawMsg) {
          // Determine log class based on content
          let logClass = 'log-entry';
          if (rawMsg === 'no' || rawMsg.includes('Invalid') || rawMsg.includes('[-]')
              || rawMsg.includes('blocked') || rawMsg.startsWith('No ')
              || rawMsg.includes('Stopped')) logClass += ' log-error';
          else if (rawMsg === 'match' || rawMsg.startsWith('Found:')
              || rawMsg.includes('VALID') || rawMsg.includes('Domains found:')) logClass += ' log-success';
          else if (rawMsg.includes('[!]') || rawMsg.includes('Sleeping') || rawMsg.includes('skip ')) logClass += ' log-warning';
          
          appState.logs.push({ text: rawMsg.replace(/\[\/?(API|\*|\+|-)\]/g, '').trim(), class: logClass });
          if (appState.logs.length > 100) appState.logs.shift(); // Keep last 100 logs
        }
        
        saveState();
        chrome.runtime.sendMessage({ action: 'stateUpdated', state: appState }).catch(() => {});
      } else if (data.type === 'result') {
        eventSource.close();
        appState.status = 'done';
        if (data.success) {
          appState.resultHtml = `<strong>✨ Found!</strong><br><br><span style="font-size:16px; font-weight:bold;">${data.email}</span>`;
          appState.resultClass = 'success';
        } else {
          appState.resultHtml = data.message || 'Not found.';
          appState.resultClass = 'error';
        }
        saveState();
        chrome.runtime.sendMessage({ action: 'stateUpdated', state: appState }).catch(() => {});
      } else if (data.type === 'done') {
        eventSource.close();
        if (appState.status === 'loading') {
          appState.status = 'done';
          saveState();
          chrome.runtime.sendMessage({ action: 'stateUpdated', state: appState }).catch(() => {});
        }
      }
    };

    eventSource.onerror = (error) => {
      eventSource.close();
      appState.status = 'done';
      appState.resultHtml = '<strong>Connection Error</strong><br><br>Make sure the Python backend server (app.py) is running locally on port 5000.';
      appState.resultClass = 'error';
      saveState();
      chrome.runtime.sendMessage({ action: 'stateUpdated', state: appState }).catch(() => {});
    };
    
    sendResponse({ success: true });
  } else if (request.action === 'stopSearch') {
    if (appState.status === 'loading') {
      fetch('http://127.0.0.1:5000/stop_search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: appState.sessionId })
      }).catch(() => {});
      
      appState.status = 'done';
      appState.resultHtml = '<strong>Search Stopped</strong><br><br>The search was cancelled by the user.';
      appState.resultClass = 'error';
      saveState();
      chrome.runtime.sendMessage({ action: 'stateUpdated', state: appState }).catch(() => {});
    }
    sendResponse({ success: true });
  }
  return true;
});
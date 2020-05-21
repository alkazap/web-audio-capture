/* eslint-disable no-plusplus */
/* eslint-disable no-alert */
/* eslint-disable no-console */
let ws = null;
let mediaRecorder = null;
const recordButton = document.getElementById('record-button');
let lastResult = '';

function sendMessage(type, data) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const message = {
      type: `${type}`,
      data: `${data}`,
    };
    console.log(`sendMessage: type=${message.type}, data=${data}`);
    ws.send(JSON.stringify(message));
  }
}

function connect() {
  const protocol = (window.location.protocol === 'https:') ? 'wss:' : 'ws:';
  const url = `${protocol}//${window.location.host}/webclient`;
  console.log(`Connecting to ${url}`);
  ws = new WebSocket(url);

  ws.addEventListener('open', () => {
    console.log('ws.onopen: Connected to the server');
    sendMessage('caps', '');
    console.log('ws.onopen: Start mediaRecorder');
    mediaRecorder.start(1000);
    recordButton.value = 'Stop';
    recordButton.disabled = false;
    console.log(`ws.onopen: mediaRecorder state=${mediaRecorder.state}`);
    console.log(`ws.onopen: recordButton value=${recordButton.value}, disabled=${recordButton.disabled}`);
  });

  ws.addEventListener('close', (event) => {
    if (event.wasClean) {
      console.log('ws.onclose: The connection closed cleanly');
    } else {
      console.log('ws.onclose: The connection did not close cleanly');
    }
    if (`${mediaRecorder.state}` === 'recording') {
      mediaRecorder.stop();
    }
    recordButton.value = 'Start';
    recordButton.disabled = false;
    console.log(`ws.onclose: recordButton value=${recordButton.value}, disabled=${recordButton.disabled}`);
  });

  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    console.log(`ws.onmessage: Got message of type ${message.type}: ${message.data}`);
    if (message.type === 'word') {
      const word = message.data;
      const responseElement = document.getElementById('response');
      if (word === '<#s>') {
        responseElement.textContent += '\n';
      } else {
        responseElement.textContent += `${word} `;
      }
      responseElement.scrollTop = responseElement.scrollHeight;
    } else if (message.type === 'result') {
      const result = message.data;
      const responseElement = document.getElementById('response');

      if (lastResult !== result) {
        if (lastResult.length > 0) {
          const text = responseElement.textContent;
          const indexEnd = text.length - lastResult.length - 1;
          responseElement.textContent = text.substring(0, indexEnd);
        }
        responseElement.textContent += `${result} `;
        lastResult = result;
      }
      if (message.final) {
        console.log('ws.onmessage: Result is final');
        responseElement.textContent += '\n';
        lastResult = '';
      }
      responseElement.scrollTop = responseElement.scrollHeight;
    } else if (message.type === 'warning' || message.type === 'error') {
      alert(`${message.data}`);
    }
  });

  ws.addEventListener('error', (event) => {
    console.log(`ws.onerror: WebSocket error: ${event.error}`);
    sendMessage('error', event.error);
  });
}

// https://developers.google.com/web/fundamentals/media/recording-audio
navigator.mediaDevices.getUserMedia({ audio: true, video: false })
  .then((stream) => {
    mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm', ignoreMutedMedia: true, audioBitsPerSecond: 16000 });
    mediaRecorder.addEventListener('dataavailable', (event) => {
      if (event.data && event.data.size > 0) {
        console.log(`mediaRecorder.ondataavailable: data.size: ${event.data.size}`);
        event.data.arrayBuffer().then((buffer) => {
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(buffer);
          }
        });
      }
    });

    mediaRecorder.addEventListener('stop', () => {
      sendMessage('eos', 'EOS');
    });

    mediaRecorder.addEventListener('error', (event) => {
      console.log(`mediaRecorder.onerror: ${event.error.name}`);
      sendMessage('error', event.error);
    });
  })
  .catch((error) => {
    console.log(`getUserMedia.onerror: ${error.name}: ${error.message}`);
    sendMessage('error', error);
  });

recordButton.addEventListener('click', () => {
  console.log(`recordButton.onclick: recordButton value=${recordButton.value}, disabled=${recordButton.disabled}`);
  if (mediaRecorder != null) {
    if (recordButton.value === 'Start') {
      connect();
    } else if (recordButton.value === 'Stop') {
      console.log('recordButton.onclick: Stop mediaRecorder');
      mediaRecorder.stop();
    }
    console.log(`recordButton.onclick: mediaRecorder state=${mediaRecorder.state}`);
  }
  recordButton.disabled = true;
  console.log(`recordButton.onclick: recordButton value=${recordButton.value}, disabled=${recordButton.disabled}`);
});

function sysInfoListener() {
  const protocol = (window.location.protocol === 'https:') ? 'wss:' : 'ws:';
  const url = `${protocol}//${window.location.host}/sysinfo`;
  console.log(`Connecting to ${url}`);
  const sysInfoWS = new WebSocket(url);

  sysInfoWS.addEventListener('open', () => {
    console.log('sysInfoWS.onopen: Connected to the server');
  });

  sysInfoWS.addEventListener('message', (event) => {
    // console.log(`sysInfoWS.onmessage: Got message: ${event.data}`);
    const sysInfoElement = document.getElementById('sysinfo');
    const message = JSON.parse(event.data);

    let cpuStats = '';
    let procStats = '';
    for (let i = 0; i < Object.keys(message).length; i++) {
      cpuStats += `CPU${i.toString().padEnd(3)} %${message[i].cpu_percent.toString().padEnd(6)} MEM %${message[i].memory_percent.toString().padEnd(6)} `;
      for (let j = 0; j < Object.keys(message[i].procs).length; j++) {
        cpuStats += `${message[i].procs[j].name.substr(0, 8).padEnd(8)}[${message[i].procs[j].pid}]-${message[i].procs[j].num_threads.toString().padEnd(6)} `;
        procStats += `${message[i].procs[j].name.substr(0, 8).padEnd(8)} ${message[i].procs[j].pid} `;
        procStats += `CPU${i.toString().padEnd(3)} %${message[i].procs[j].cpu_percent.toString().padEnd(6)} `;
        procStats += `MEM %${message[i].procs[j].memory_percent.toString().padEnd(6)} `;
        procStats += `Nthreads ${message[i].procs[j].num_threads.toString().padEnd(6)}\n`;
      }
      cpuStats += '\n';
    }
    cpuStats += '\n';
    sysInfoElement.textContent = cpuStats + procStats;
  });
}

window.onload = sysInfoListener();

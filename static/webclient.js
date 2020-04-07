/* eslint-disable no-alert */
/* eslint-disable no-console */

let ws = null;
let mediaRecorder = null;

const testButton = document.getElementById('test-button');
const recordButton = document.getElementById('record-button');

testButton.addEventListener('click', () => {
  console.log(`testButton.onclick: testButton.value=${testButton.value}, testButton.disabled=${testButton.disabled}`);
  console.log(`testButton.onclick: recordButton.value=${recordButton.value}, recordButton.diabled=${recordButton.disabled}`);
  if (testButton.value === 'Start') {
    const file = document.getElementById('audio-file').value;
    if (file.length > 0) {
      const formats = ['raw', 'wav', 'ogg', 'mp3'];
      const format = file.split('.').pop();
      if (formats.includes(format)) {
        const caps = ((format === 'raw') ? 'audio/x-raw, layout=(string)interleaved, rate=(int)16000, format=(string)S16LE, channels=(int)1' : ''); // `audio/x-${format}`)
        const message = {
          type: 'init_request',
          caps: `${caps}`,
          file: `${file}`,
        };
        console.log(`testButton.onclick: send message: type=${message.type}, file=${file}, caps=${caps}`);
        ws.send(JSON.stringify(message));
        testButton.value = 'Stop';
        recordButton.disabled = true;
      } else {
        console.log(`testButton.onclick: Unsupported file format: ${format}`);
      }
    } else {
      alert('Select a file!');
    }
  } else if (testButton.value === 'Stop') {
    const message = {
      type: 'eos',
    };
    console.log(`testButton.onclick: send message: type=${message.type}`);
    ws.send(JSON.stringify(message));
    testButton.value = 'Start';
    recordButton.disabled = false;
  }
  console.log(`testButton.onclick: testButton.value=${testButton.value}, testButton.disabled=${testButton.disabled}`);
  console.log(`testButton.onclick: recordButton.value=${recordButton.value}, recordButton.diabled=${recordButton.disabled}`);
});

recordButton.addEventListener('click', () => {
  console.log(`recordButton.onclick: recordButton.value=${recordButton.value}, recordButton.diabled=${recordButton.disabled}`);
  console.log(`recordButton.onclick: testButton.value=${testButton.value}, testButton.disabled=${testButton.disabled}`);
  if (recordButton.value === 'Start') {
    const message = {
      type: 'init_request',
      caps: '',
    };
    console.log(`recordButton.onclick: send message: type=${message.type}, caps=${message.caps}`);
    ws.send(JSON.stringify(message));
    recordButton.value = 'Stop';
    testButton.disabled = true;
    if (mediaRecorder != null) {
      mediaRecorder.start(1000);
    }
  } else if (recordButton.value === 'Stop') {
    const message = {
      type: 'eos',
    };
    console.log(`recordButton.onclick: send message: type=${message.type}`);
    ws.send(JSON.stringify(message));
    recordButton.value = 'Start';
    testButton.disabled = false;
    if (mediaRecorder != null) {
      mediaRecorder.stop();
    }
  }
  console.log(`recordButton.onclick: recordButton.value=${recordButton.value}, recordButton.diabled=${recordButton.disabled}`);
  console.log(`recordButton.onclick: testButton.value=${testButton.value}, testButton.disabled=${testButton.disabled}`);
  console.log(`recordButton.onclick: mediaRecorder.state: ${mediaRecorder.state}`);
});

function connect() {
  console.log(`connect to ws://${document.location.host}/webclient`);
  ws = new WebSocket(`ws://${document.location.host}/webclient`);
  ws.addEventListener('open', () => {
    console.log('ws.onopen: Connected to the server');
  });
  ws.addEventListener('close', () => {
    console.log('ws.onclose: WebSocket is closed now');
    if (mediaRecorder != null) {
      if (mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
      }
    }
    testButton.value = 'Start';
    recordButton.value = 'Start';
    testButton.disabled = false;
    recordButton.disabled = false;
    connect();
  });
  ws.addEventListener('error', (event) => {
    console.log(`ws.onerror: WebSocket error: ${event}`);
    const message = {
      type: 'error',
      data: event,
    };
    ws.send(JSON.stringify(message));
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
    } else if (message.type === 'eos') {
      ws.send(JSON.stringify(message));
    } else if (message.type === 'error') {
      console.log(`onmessage(): Decoder error: ${message.data}`);
    }
  });
}

function record() {
  // https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia
  // Older browsers might not implement mediaDevices at all, so we set an empty object first
  if (navigator.mediaDevices === undefined) {
    navigator.mediaDevices = {};
  }
  // Some browsers partially implement mediaDevices. We can't just assign an object
  // with getUserMedia as it would overwrite existing properties.
  // Here, we will just add the getUserMedia property if it's missing.
  if (navigator.mediaDevices.getUserMedia === undefined) {
    navigator.mediaDevices.getUserMedia = (constraints) => {
      // First get ahold of the legacy getUserMedia, if present
      const getUserMedia = navigator.webkitGetUserMedia || navigator.mozGetUserMedia;

      // Some browsers just don't implement it - return a rejected promise with an error
      // to keep a consistent interface
      if (!getUserMedia) {
        return Promise.reject(new Error('getUserMedia is not implemented in this browser'));
      }

      // Otherwise, wrap the call to the old navigator.getUserMedia with a Promise
      return new Promise(((resolve, reject) => {
        getUserMedia.call(navigator, constraints, resolve, reject);
      }));
    };
  }
  // https://developers.google.com/web/fundamentals/media/recording-audio
  navigator.mediaDevices.getUserMedia({ audio: true, video: false })
    .then((stream) => {
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm', ignoreMutedMedia: true, audioBitsPerSecond: 16000 });
      mediaRecorder.addEventListener('dataavailable', (event) => {
        if (testButton.disabled === true && recordButton.disabled === false) {
          if (event.data && event.data.size > 0) {
            console.log(`mediaRecorder.ondataavailable: event.data.size: ${event.data.size}`);
            event.data.arrayBuffer().then((buffer) => ws.send(buffer));
          }
        }
      });
      mediaRecorder.addEventListener('error', (event) => {
        console.log(`mediaRecorder.onerror: MediaRecorder error: ${event}`);
      });
    })
    .catch((error) => {
      console.log(`getUserMedia error: ${error.message}`);
    });
}

window.addEventListener('load', () => {
  connect();
  record();
});

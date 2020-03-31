var ws = null;

window.onload = function() {
    connect();
};

function connect(){
    ws = new WebSocket("ws://" + document.location.host + "/webclient");
    ws.onopen = function(event) {
        alert(`onopen(): Connected to the server`);
        //ws.send("Hello from ClientSocket");
    };
    ws.onclose = function(event) {
        alert(`onclose(): WebSocket is closed now`);
    };
    ws.onerror = function(event) {
        alert(`onerror(): WebSocket error: ${event}`);
        var message = {
            type : "error",
            data : event      
        };
        ws.send(JSON.stringify(message));
    };
    ws.onmessage = function(event) {
        var message = JSON.parse(event.data);
        if (message.type == "word"){
            var word = message.data;
            responseElement = document.getElementById("response");
            if (word == "<#s>"){
                responseElement.textContent += "\n";
            } else {
                responseElement.textContent += word + " ";
            }
        } else if (message.type == "eos"){
            ws.send(JSON.stringify(message))
        } else if (message.type == "error"){
            alert(`onmessage(): Decoder error: ${message.data}`);
        }
    };
}
function init_request(){
    var message = {
        type : "init_request",
        file : document.getElementById("audio-file").value,
        caps : document.getElementById("content-type").value,
        rate : document.getElementById("rate").value       
    };
    ws.send(JSON.stringify(message));
}



// https://developers.google.com/web/fundamentals/media/recording-audio
if(navigator.mediaDevices){
    navigator.mediaDevices.getUserMedia({ audio: true, video: false })
    .then(function(stream) {
        const context = new AudioContext();
        const source = context.createMediaStreamSource(stream);
        // buggerSize must be a power of 2 between 256 and 16384 (try 4096, 8192)
        const processor = context.createScriptProcessor(8192, 1, 1);

        source.connect(processor);
        processor.connect(context.destination);

        processor.onaudioprocess = function (e) {
            ws.send(e.inputBuffer);
        };
    })
    .catch(function(error) {
        alert(`getUserMedia error: ${error.message}`)
    });
} else {
    alert(`getUserMedia not supported on your browser!`)
}

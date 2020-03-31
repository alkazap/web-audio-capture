// Fetch an array of devices of a certain type
async function getConnectedDevices(type) {
    const devices = await navigator.mediaDevices.enumerateDevices();
    return devices.filter(device => device.kind === type)
}

// Updates the select element with the provided set of devices
function updateDeviceList(type) {
    const devices = getConnectedDevices(type);
    const deviceList = document.querySelector('select#availableDevice');
    deviceList.innerHTML = '';
    devices.map(device => {
        const deviceOption = document.createElement('option');
        deviceOption.label = device.label;
        deviceOption.value = device.deviceId;
    }).forEach(deviceOption => deviceList.add(deviceOption));
}

// Get the initial set of devices connected
updateDeviceList('audioinput');

// Listen for changes to media devices and update the list accordingly
navigator.mediaDevices.ondevicechange = function(event) {
    updateDeviceList('audioinput');
}

const openMediaDevices = async (constraints) => {
    return await navigator.mediaDevices.getUserMedia(constraints);
}

try {
    const stream = openMediaDevices({'audio':true, 'video':false});
    console.log('Got MediaStream:', stream);
} catch(error) {
    console.error('Error accessing media devices.', error);
}


import QtQuick
import org.kde.pipewire as Pipewire
import Caelestia.Services

// A live picture of one KWin window: KWin's zkde_screencast stream through Caelestia's WindowStream, painted by
// KPipeWire. Loaded on demand by the Dock's window previews; without Caelestia's plugin the load fails and the
// preview keeps its silhouette.
Item {
    id: root
    property string uuid: ""
    property bool active: true
    // Fades in once a frame has arrived, so the silhouette under it never blinks out first.
    readonly property bool ready: stream.available && frame.ready
    readonly property real aspect: frame.streamSize.width > 0 && frame.streamSize.height > 0
        ? frame.streamSize.width / frame.streamSize.height : 0

    WindowStream {
        id: stream
        address: root.uuid
        active: root.active && root.uuid.length > 0
    }

    Pipewire.PipeWireSourceItem {
        id: frame
        anchors.fill: parent
        opacity: root.ready ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        // objectSerial is what an unprivileged client can bind; older KPipeWire only has nodeId.
        Component.onCompleted: {
            if ("objectSerial" in frame) frame.objectSerial = Qt.binding(() => stream.objectSerial)
            else frame.nodeId = Qt.binding(() => stream.nodeId)
        }
    }
}

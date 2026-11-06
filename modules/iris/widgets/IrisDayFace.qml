pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import qs.services
import qs.modules.common.functions
import qs.modules.common.widgets
import qs.modules.iris.style
import qs.modules.iris.bar.island

IrisWidgetFace {
    id: root

    readonly property real fraction: root.widget.dayFraction
    readonly property int percent: Math.floor(root.fraction * 100)
    readonly property int minutesLeft: Math.max(0, Math.round((1 - root.fraction) * 1440))
    readonly property string remaining: {
        const hours = Math.floor(root.minutesLeft / 60)
        const minutes = root.minutesLeft % 60
        const span = hours > 0 ? Translation.tr("%1 h %2 min").arg(hours).arg(minutes) : Translation.tr("%1 min").arg(minutes)
        return Translation.tr("%1 left").arg(span)
    }
    readonly property real sunrise: root.dayShare(Weather.data?.sunrise, 6 / 24)
    readonly property real sunset: root.dayShare(Weather.data?.sunset, 19 / 24)

    // The sun face (iris.face "sun", small only): the next sunrise or sunset over the sun's arc through the day.
    readonly property bool sun: root.small && String(root.widget.irisOption("face", "ring")) === "sun"
    readonly property bool sunUp: root.fraction >= root.sunrise && root.fraction < root.sunset
    readonly property real nextEvent: root.sunUp ? root.sunset : root.sunrise
    readonly property real otherEvent: root.sunUp ? root.sunrise : root.sunset

    // A day share as the locale's short time, split into the clock and its AM/PM mark.
    function clock(share: real): var {
        const minutes = Math.round(share * 1440) % 1440
        const date = new Date(2000, 0, 1, Math.floor(minutes / 60), minutes % 60)
        const text = Translation.locale.toString(date, Translation.locale.timeFormat(Locale.ShortFormat))
        const match = text.match(/^(.*?)\s*([AaPp]\.?\s?[Mm]\.?)$/)
        return match ? { time: match[1], mark: match[2].toUpperCase() } : { time: text, mark: "" }
    }
    function dayShare(text: var, fallback: real): real {
        const match = String(text ?? "").match(/(\d{1,2}):(\d{2})\s*([AaPp][Mm])?/)
        if (!match)
            return fallback
        let hours = Number(match[1]) % 12
        if (!match[3] || match[3].toLowerCase() === "am")
            hours = match[3] ? hours : Number(match[1])
        else
            hours += 12
        return (hours * 60 + Number(match[2])) / 1440
    }

    component SunTime: Row {
        id: sunTime
        property real share: 0
        property real figureSize: 30
        property color ink: root.ink
        readonly property var parts: root.clock(sunTime.share)
        spacing: root.dp(3)
        FaceFigure {
            id: sunFigure
            face: root
            text: sunTime.parts.time
            size: sunTime.figureSize
            color: sunTime.ink
        }
        FaceText {
            face: root
            visible: text.length > 0
            text: sunTime.parts.mark
            color: root.inkSecondary
            size: Math.max(9, sunTime.figureSize * 0.4)
            weight: Font.DemiBold
            y: Math.round(sunFigure.baselineOffset - baselineOffset)
        }
    }

    Column {
        visible: root.sun
        anchors.fill: parent
        spacing: 0

        FaceHeader {
            face: root
            width: parent.width
            glyph: "wb_twilight"
            text: root.sunUp ? Translation.tr("Sunset") : Translation.tr("Sunrise")
            tint: root.warm
        }
        SunTime {
            share: root.nextEvent
        }
        Shape {
            id: arc
            width: parent.width
            height: Math.max(0, parent.height - y - sunFooter.height - root.dp(4))
            preferredRendererType: Shape.CurveRenderer
            readonly property real peak: root.dp(6)
            // One smooth cosine through the day: noon at the top, midnight at the floor, the horizon where the sun crosses it.
            readonly property real floor: arc.height - root.dp(3)
            readonly property real horizon: arc.at(root.sunrise).y
            function at(share: real): point {
                const noon = (root.sunrise + root.sunset) / 2
                const lift = (Math.cos(2 * Math.PI * (share - noon)) + 1) / 2
                return Qt.point(share * arc.width, arc.floor - lift * (arc.floor - arc.peak))
            }
            function line(from: real, to: real): var {
                const points = []
                const steps = Math.max(2, Math.ceil((to - from) * 64))
                for (let i = 0; i <= steps; i++)
                    points.push(arc.at(from + (to - from) * i / steps))
                return points
            }
            ShapePath {
                strokeColor: root.fill
                strokeWidth: 1
                fillColor: "transparent"
                PathPolyline { path: [Qt.point(0, arc.horizon), Qt.point(arc.width, arc.horizon)] }
            }
            ShapePath {
                strokeColor: ColorUtils.applyAlpha(root.ink, 0.22)
                strokeWidth: root.dp(2)
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                fillColor: "transparent"
                PathPolyline { path: arc.line(root.fraction, 1) }
            }
            ShapePath {
                strokeColor: root.warm
                strokeWidth: root.dp(2)
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                fillColor: "transparent"
                PathPolyline { path: arc.line(0, root.fraction) }
            }
            Rectangle {
                readonly property point spot: arc.at(root.fraction)
                width: root.dp(7)
                height: width
                radius: width / 2
                x: spot.x - width / 2
                y: spot.y - height / 2
                color: root.sunUp ? root.warm : root.inkSecondary
            }
        }
        Item { width: 1; height: root.dp(4) }
        Row {
            id: sunFooter
            spacing: root.dp(5)
            FaceText {
                face: root
                text: root.sunUp ? Translation.tr("Sunrise") : Translation.tr("Sunset")
                color: root.inkSecondary
                size: 12
                y: Math.round(otherTime.y + otherTime.height - height - root.dp(2))
            }
            SunTime {
                id: otherTime
                share: root.otherEvent
                figureSize: 15
            }
        }
    }

    ColumnLayout {
        visible: root.small && !root.sun
        anchors.fill: parent
        spacing: root.dp(6)
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            ProgressRing {
                anchors.centerIn: parent
                width: Math.min(parent.width, parent.height)
                height: width
                progress: root.fraction
                tint: root.warm
                stroke: Math.max(4, root.dp(9))
            }
            ColumnLayout {
                anchors.centerIn: parent
                spacing: -root.dp(2)
                Row {
                    Layout.alignment: Qt.AlignHCenter
                    spacing: root.dp(1)
                    FaceFigure {
                        id: compactPercent
                        face: root
                        text: root.percent
                        size: 32
                    }
                    FaceText {
                        face: root
                        anchors.baseline: compactPercent.baseline
                        text: "%"
                        color: root.inkSecondary
                        size: 14
                    }
                }
                FaceText {
                    face: root
                    Layout.alignment: Qt.AlignHCenter
                    text: Translation.tr("of today")
                    color: root.inkTertiary
                    size: 11
                }
            }
        }
        FaceText {
            face: root
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            text: root.remaining
            color: root.inkSecondary
            size: 12
        }
    }

    ColumnLayout {
        visible: !root.small
        anchors.fill: parent
        spacing: 0
        FaceHeader {
            face: root
            Layout.fillWidth: true
            glyph: root.widget.isDaytime ? "light_mode" : "bedtime"
            text: IrisFaceData.capitalized(Translation.locale.toString(DateTime.clock.date, "dddd d"))
            tint: root.warm
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: root.dp(10)
            Row {
                spacing: root.dp(2)
                FaceFigure {
                    id: dayPercent
                    face: root
                    text: root.percent
                    size: 48
                }
                FaceText {
                    face: root
                    anchors.baseline: dayPercent.baseline
                    text: "%"
                    color: root.inkSecondary
                    size: 22
                }
            }
            FaceText {
                face: root
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignBottom
                Layout.bottomMargin: root.dp(8)
                text: root.remaining
                color: root.inkSecondary
                size: 12.5
            }
        }
        Item { Layout.fillHeight: true }
        Item {
            id: track
            Layout.fillWidth: true
            Layout.preferredHeight: root.dp(26)
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                height: root.dp(8)
                radius: height / 2
                color: root.fill
                Rectangle {
                    width: parent.width * root.fraction
                    height: parent.height
                    radius: height / 2
                    color: root.warm
                }
            }
            Repeater {
                model: [{ at: root.sunrise, glyph: "wb_twilight" }, { at: root.sunset, glyph: "wb_twilight" }]
                MaterialSymbol {
                    required property var modelData
                    x: track.width * modelData.at - width / 2
                    y: root.dp(4) - height
                    text: modelData.glyph
                    fill: 1
                    iconSize: root.px(13)
                    color: root.warm
                }
            }
            Rectangle {
                x: track.width * root.fraction - width / 2
                anchors.verticalCenter: parent.verticalCenter
                width: root.dp(4)
                height: root.dp(18)
                radius: width / 2
                color: root.ink
            }
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 0
            Repeater {
                model: [0, 6, 12, 18, 24]
                FaceText {
                    required property int modelData
                    required property int index
                    face: root
                    Layout.fillWidth: true
                    horizontalAlignment: index === 0 ? Text.AlignLeft : index === 4 ? Text.AlignRight : Text.AlignHCenter
                    text: modelData
                    color: root.inkTertiary
                    size: 10.5
                    font.family: root.fontNumbers
                    font.features: ({ "tnum": 1 })
                }
            }
        }
    }
}

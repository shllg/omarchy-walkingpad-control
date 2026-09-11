import QtQuick
import QtTest

// Parent this helper to a real window's focus item. TestEvent delivers Qt
// events there; it never calls the production click or key handlers directly.
Item {
  TestEvent { id: events }

  function click(item) {
    return events.mouseClick(item, item.width / 2, item.height / 2,
                             Qt.LeftButton, Qt.NoModifier, -1)
  }

  function key(name) {
    var keys = ({ Escape: Qt.Key_Escape, Return: Qt.Key_Return, Enter: Qt.Key_Enter,
      Up: Qt.Key_Up, Down: Qt.Key_Down, Left: Qt.Key_Left, Right: Qt.Key_Right,
      Home: Qt.Key_Home, End: Qt.Key_End,
      Space: Qt.Key_Space, F5: Qt.Key_F5 })
    if (keys[name] === undefined) throw new Error("Unknown key: " + name)
    return events.keyClick(keys[name], Qt.NoModifier, -1)
  }

  function typeText(value) {
    if (!events.keyClick(Qt.Key_A, Qt.ControlModifier, -1)) return false
    for (var i = 0; i < value.length; ++i) {
      if (!events.keyClickChar(value[i], Qt.NoModifier, -1)) return false
    }
    return true
  }
}

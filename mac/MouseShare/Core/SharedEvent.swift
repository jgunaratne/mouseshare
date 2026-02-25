import Foundation

/// Types of input events that can be shared between machines.
enum SharedEventType: String, Codable {
    case mouseMove
    case leftMouseDown
    case leftMouseUp
    case rightMouseDown
    case rightMouseUp
    case keyDown
    case keyUp
    case scrollWheel
    case returnControl
}

/// A single input event with normalized coordinates and optional keyboard/scroll data.
/// Coordinates are normalized to 0–1 so different screen sizes map correctly.
struct SharedEvent: Codable {
    let type: SharedEventType
    
    /// Mouse X position normalized to 0–1 (0 = left edge, 1 = right edge)
    let normalizedX: Double
    
    /// Mouse Y position normalized to 0–1 (0 = top edge, 1 = bottom edge)
    let normalizedY: Double
    
    /// Virtual key code for keyboard events (e.g. 53 = Escape)
    let keyCode: Int?
    
    /// Modifier flags (shift, cmd, ctrl, option) as a raw bitmask
    let modifierFlags: UInt64?
    
    /// Horizontal scroll delta
    let scrollDeltaX: Double?
    
    /// Vertical scroll delta
    let scrollDeltaY: Double?
    
    init(
        type: SharedEventType,
        normalizedX: Double = 0,
        normalizedY: Double = 0,
        keyCode: Int? = nil,
        modifierFlags: UInt64? = nil,
        scrollDeltaX: Double? = nil,
        scrollDeltaY: Double? = nil
    ) {
        self.type = type
        self.normalizedX = normalizedX
        self.normalizedY = normalizedY
        self.keyCode = keyCode
        self.modifierFlags = modifierFlags
        self.scrollDeltaX = scrollDeltaX
        self.scrollDeltaY = scrollDeltaY
    }
}

import Cocoa
import CoreGraphics

/// Injects SharedEvents into the local Mac as system-level input events.
/// This class is a stub for future bidirectional support — currently the Mac only
/// sends events to Linux and does not receive them back.
class EventInjector {
    
    /// Inject the given SharedEvent into the local system.
    func inject(_ event: SharedEvent) {
        guard let screen = NSScreen.main else { return }
        let screenSize = screen.frame.size
        
        // Convert normalized coordinates back to screen coordinates
        let x = CGFloat(event.normalizedX) * screenSize.width
        let y = CGFloat(event.normalizedY) * screenSize.height
        let point = CGPoint(x: x, y: y)
        
        switch event.type {
        case .mouseMove:
            postMouseEvent(type: .mouseMoved, at: point, button: .left)
            
        case .leftMouseDown:
            postMouseEvent(type: .leftMouseDown, at: point, button: .left)
            
        case .leftMouseUp:
            postMouseEvent(type: .leftMouseUp, at: point, button: .left)
            
        case .rightMouseDown:
            postMouseEvent(type: .rightMouseDown, at: point, button: .right)
            
        case .rightMouseUp:
            postMouseEvent(type: .rightMouseUp, at: point, button: .right)
            
        case .keyDown:
            postKeyEvent(keyDown: true, keyCode: event.keyCode ?? 0, modifiers: event.modifierFlags)
            
        case .keyUp:
            postKeyEvent(keyDown: false, keyCode: event.keyCode ?? 0, modifiers: event.modifierFlags)
            
        case .scrollWheel:
            postScrollEvent(deltaX: event.scrollDeltaX ?? 0, deltaY: event.scrollDeltaY ?? 0)
            
        case .returnControl:
            // No local action needed for returnControl
            break
        }
    }
    
    // MARK: - Private Helpers
    
    private func postMouseEvent(type: CGEventType, at point: CGPoint, button: CGMouseButton) {
        guard let cgEvent = CGEvent(
            mouseEventSource: nil,
            mouseType: type,
            mouseCursorPosition: point,
            mouseButton: button
        ) else { return }
        
        cgEvent.post(tap: CGEventTapLocation.cghidEventTap)
    }
    
    private func postKeyEvent(keyDown: Bool, keyCode: Int, modifiers: UInt64?) {
        guard let cgEvent = CGEvent(
            keyboardEventSource: nil,
            virtualKey: CGKeyCode(keyCode),
            keyDown: keyDown
        ) else { return }
        
        if let modifiers = modifiers {
            cgEvent.flags = CGEventFlags(rawValue: modifiers)
        }
        
        cgEvent.post(tap: CGEventTapLocation.cghidEventTap)
    }
    
    private func postScrollEvent(deltaX: Double, deltaY: Double) {
        guard let cgEvent = CGEvent(
            scrollWheelEvent2Source: nil,
            units: .pixel,
            wheelCount: 2,
            wheel1: Int32(deltaY),
            wheel2: Int32(deltaX),
            wheel3: 0
        ) else { return }
        
        cgEvent.post(tap: CGEventTapLocation.cghidEventTap)
    }
}

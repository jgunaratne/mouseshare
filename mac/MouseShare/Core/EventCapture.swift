import Cocoa
import CoreGraphics

/// Captures system-wide keyboard and mouse events using CGEventTap.
/// All captured events are consumed (not forwarded to local apps) while active.
class EventCapture {
    
    /// Called with each captured event. The event has already been consumed at the system level.
    var onEvent: ((SharedEvent) -> Void)?
    
    fileprivate var eventTap: CFMachPort?
    private var runLoopSource: CFRunLoopSource?
    
    /// Start intercepting all keyboard and mouse events system-wide.
    /// Requires Accessibility permission — logs an error if the tap cannot be created.
    func start() {
        guard eventTap == nil else { return }
        
        let mouseMask: CGEventMask =
            (1 << CGEventType.mouseMoved.rawValue) |
            (1 << CGEventType.leftMouseDragged.rawValue) |
            (1 << CGEventType.rightMouseDragged.rawValue)
        
        let buttonMask: CGEventMask =
            (1 << CGEventType.leftMouseDown.rawValue) |
            (1 << CGEventType.leftMouseUp.rawValue) |
            (1 << CGEventType.rightMouseDown.rawValue) |
            (1 << CGEventType.rightMouseUp.rawValue)
        
        let keyMask: CGEventMask =
            (1 << CGEventType.keyDown.rawValue) |
            (1 << CGEventType.keyUp.rawValue) |
            (1 << CGEventType.scrollWheel.rawValue)
        
        let eventMask: CGEventMask = mouseMask | buttonMask | keyMask
        
        // Store `self` in an Unmanaged pointer so the C callback can access it
        let selfPtr = Unmanaged.passUnretained(self).toOpaque()
        
        guard let tap = CGEvent.tapCreate(
            tap: .cgSessionEventTap,
            place: .headInsertEventTap,
            options: .defaultTap,
            eventsOfInterest: eventMask,
            callback: eventTapCallback,
            userInfo: selfPtr
        ) else {
            print("❌ [EventCapture] Failed to create event tap.")
            print("   → Open System Settings → Privacy & Security → Accessibility")
            print("   → Add MouseShare and grant permission, then relaunch.")
            return
        }
        
        eventTap = tap
        runLoopSource = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), runLoopSource, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
        
        print("✅ [EventCapture] Event tap started — all input is being captured.")
    }
    
    /// Stop intercepting events and return input to local apps.
    func stop() {
        if let tap = eventTap {
            CGEvent.tapEnable(tap: tap, enable: false)
            if let source = runLoopSource {
                CFRunLoopRemoveSource(CFRunLoopGetCurrent(), source, .commonModes)
            }
            // CFMachPort is managed by the system; nil out our references
        }
        eventTap = nil
        runLoopSource = nil
        print("⏹ [EventCapture] Event tap stopped — input returned to Mac.")
    }
    
    deinit {
        stop()
    }
    
    // MARK: - Internal Event Processing
    
    /// Process a raw CGEvent and return nil to consume it (prevent local delivery).
    fileprivate func handleEvent(type: CGEventType, event: CGEvent) -> CGEvent? {
        guard let screen = NSScreen.main else { return nil }
        let screenSize = screen.frame.size
        let mouseLocation = event.location
        
        let normalizedX = Double(mouseLocation.x / screenSize.width).clamped(to: 0...1)
        // CoreGraphics Y is top-down, normalize accordingly
        let normalizedY = Double(mouseLocation.y / screenSize.height).clamped(to: 0...1)
        
        let sharedEvent: SharedEvent
        
        switch type {
        case .mouseMoved, .leftMouseDragged, .rightMouseDragged:
            sharedEvent = SharedEvent(
                type: .mouseMove,
                normalizedX: normalizedX,
                normalizedY: normalizedY
            )
            
        case .leftMouseDown:
            sharedEvent = SharedEvent(type: .leftMouseDown, normalizedX: normalizedX, normalizedY: normalizedY)
            
        case .leftMouseUp:
            sharedEvent = SharedEvent(type: .leftMouseUp, normalizedX: normalizedX, normalizedY: normalizedY)
            
        case .rightMouseDown:
            sharedEvent = SharedEvent(type: .rightMouseDown, normalizedX: normalizedX, normalizedY: normalizedY)
            
        case .rightMouseUp:
            sharedEvent = SharedEvent(type: .rightMouseUp, normalizedX: normalizedX, normalizedY: normalizedY)
            
        case .keyDown, .keyUp:
            let keyCode = Int(event.getIntegerValueField(.keyboardEventKeycode))
            let modifiers = event.flags.rawValue
            
            // Escape key (keyCode 53) → return control to Mac
            if keyCode == 53 {
                let returnEvent = SharedEvent(type: .returnControl)
                onEvent?(returnEvent)
                return nil // consume the Escape key
            }
            
            let eventType: SharedEventType = (type == .keyDown) ? .keyDown : .keyUp
            sharedEvent = SharedEvent(
                type: eventType,
                normalizedX: normalizedX,
                normalizedY: normalizedY,
                keyCode: keyCode,
                modifierFlags: modifiers
            )
            
        case .scrollWheel:
            let deltaY = event.getDoubleValueField(.scrollWheelEventDeltaAxis1)
            let deltaX = event.getDoubleValueField(.scrollWheelEventDeltaAxis2)
            sharedEvent = SharedEvent(
                type: .scrollWheel,
                normalizedX: normalizedX,
                normalizedY: normalizedY,
                scrollDeltaX: deltaX,
                scrollDeltaY: deltaY
            )
            
        default:
            return nil
        }
        
        onEvent?(sharedEvent)
        return nil // consume all events while capturing
    }
}

// MARK: - C Callback

/// Global C-function callback for the CGEventTap.
private func eventTapCallback(
    proxy: CGEventTapProxy,
    type: CGEventType,
    event: CGEvent,
    userInfo: UnsafeMutableRawPointer?
) -> Unmanaged<CGEvent>? {
    
    // If the tap is disabled by the system (e.g. timeout), re-enable it
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let userInfo = userInfo {
            let capture = Unmanaged<EventCapture>.fromOpaque(userInfo).takeUnretainedValue()
            if let tap = capture.eventTap {
                CGEvent.tapEnable(tap: tap, enable: true)
            }
        }
        return Unmanaged.passRetained(event)
    }
    
    guard let userInfo = userInfo else {
        return Unmanaged.passRetained(event)
    }
    
    let capture = Unmanaged<EventCapture>.fromOpaque(userInfo).takeUnretainedValue()
    
    if let result = capture.handleEvent(type: type, event: event) {
        return Unmanaged.passRetained(result)
    }
    
    return nil // event consumed
}

// MARK: - Double Clamping Helper

private extension Double {
    func clamped(to range: ClosedRange<Double>) -> Double {
        return min(max(self, range.lowerBound), range.upperBound)
    }
}

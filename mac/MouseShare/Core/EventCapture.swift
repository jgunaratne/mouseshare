import Cocoa
import CoreGraphics

// ── Serial queue for work that must NOT block the event-tap callback ──
// macOS disables the tap when the callback takes >~500ms, causing keyboard
// events to leak through to local Mac apps. All slow operations (cursor warp,
// JSON encode, TCP send) are dispatched here.
private let eventDispatchQueue = DispatchQueue(label: "com.mouseshare.eventDispatch", qos: .userInteractive)

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
            (1 << CGEventType.scrollWheel.rawValue) |
            (1 << CGEventType.flagsChanged.rawValue)
        
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
        }
        eventTap = nil
        runLoopSource = nil
        print("⏹ [EventCapture] Event tap stopped — input returned to Mac.")
    }
    
    deinit {
        stop()
    }
    
    /// Whether the cursor should be pinned and movement sent to Linux via deltas.
    var hideCursor = false
    
    /// The position to pin the Mac cursor at (set before calling start()).
    var pinnedPosition: CGPoint = .zero
    
    /// Virtual cursor position on the Linux screen (normalized 0–1).
    /// Updated by raw mouse deltas while hideCursor is true.
    var virtualX: Double = 0.0
    var virtualY: Double = 0.0
    
    /// Previous modifier flags — used to detect which modifier key
    /// was pressed or released in flagsChanged events.
    private var previousFlags: UInt64 = 0
    
    /// Thread-safe flag: set from TCP background queue when Linux sends
    /// returnControl.  The event-tap callback checks this on every event
    /// so it can break out of the capture loop even when the main thread
    /// is saturated by CGWarpMouseCursorPosition events.
    var shouldReturnControl = false
    
    /// Which Mac screen edge triggered the switch.  Determines which
    /// virtual-cursor boundary returns control (the opposite edge).
    var selectedEdge: ScreenEdge = .right
    
    /// Minimum interval between mouse move events (seconds).
    /// ~120 fps = 1/120 ≈ 0.0083s.
    private let mouseSendInterval: Double = 1.0 / 120.0
    
    /// Timestamp of the last mouse move event we actually sent.
    private var lastMouseSendTime: CFAbsoluteTime = 0
    
    /// Dispatch the onEvent callback off the event-tap thread.
    /// This prevents JSON encoding + TCP send from blocking the callback.
    private func dispatchEvent(_ event: SharedEvent) {
        let handler = onEvent
        eventDispatchQueue.async {
            handler?(event)
        }
    }
    
    // MARK: - Internal Event Processing
    
    /// Process a raw CGEvent and return nil to consume it (prevent local delivery).
    fileprivate func handleEvent(type: CGEventType, event: CGEvent) -> CGEvent? {
        // Check the thread-safe flag before doing anything else.
        if shouldReturnControl {
            shouldReturnControl = false
            let returnEvent = SharedEvent(type: .returnControl)
            dispatchEvent(returnEvent)
            return nil
        }
        
        guard let screen = NSScreen.main else { return nil }
        let screenSize = screen.frame.size
        
        // Compute the normalized position to use for this event.
        // When controlling Linux, use the virtual position driven by deltas.
        // Otherwise, use the actual Mac cursor location.
        let normalizedX: Double
        let normalizedY: Double
        
        if hideCursor {
            // Use raw mouse deltas to update the virtual Linux cursor position.
            // This keeps the Mac cursor frozen at the edge.
            let deltaX = event.getDoubleValueField(.mouseEventDeltaX)
            let deltaY = event.getDoubleValueField(.mouseEventDeltaY)
            
            virtualX += deltaX / Double(screenSize.width)
            virtualY += deltaY / Double(screenSize.height)
            virtualX = virtualX.clamped(to: 0...1)
            virtualY = virtualY.clamped(to: 0...1)
            
            // If the virtual cursor hit the opposite edge of the companion
            // screen, return control to Mac immediately.
            let shouldReturn: Bool
            switch selectedEdge {
            case .right:  shouldReturn = virtualX <= 0   // companion is to the right → return on its left
            case .left:   shouldReturn = virtualX >= 1   // companion is to the left  → return on its right
            case .top:    shouldReturn = virtualY >= 1   // companion is above        → return on its bottom
            case .bottom: shouldReturn = virtualY <= 0   // companion is below        → return on its top
            }
            if shouldReturn {
                let returnEvent = SharedEvent(type: .returnControl)
                dispatchEvent(returnEvent)
                return nil
            }
            
            // Warp the Mac cursor back to the pinned position so it doesn't move.
            // IMPORTANT: Dispatch asynchronously to avoid blocking the event-tap
            // callback. If the callback takes > ~500ms, macOS disables the tap
            // and keyboard events leak to local Mac apps.
            let pin = pinnedPosition
            eventDispatchQueue.async {
                CGWarpMouseCursorPosition(pin)
            }
            
            normalizedX = virtualX
            normalizedY = virtualY
        } else {
            let mouseLocation = event.location
            normalizedX = Double(mouseLocation.x / screenSize.width).clamped(to: 0...1)
            normalizedY = Double(mouseLocation.y / screenSize.height).clamped(to: 0...1)
        }
        
        let sharedEvent: SharedEvent
        
        switch type {
        case .mouseMoved, .leftMouseDragged, .rightMouseDragged:
            // Throttle mouse moves to ~120/s to avoid flooding the TCP pipe.
            let now = CFAbsoluteTimeGetCurrent()
            if now - lastMouseSendTime < mouseSendInterval {
                return nil  // skip this event, we'll catch up on the next one
            }
            lastMouseSendTime = now
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
                dispatchEvent(returnEvent)
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
            
        case .flagsChanged:
            // Modifier keys (Shift, Ctrl, Option, Command) fire flagsChanged
            // instead of keyDown/keyUp. Determine press vs release by checking
            // whether the modifier bit was added or removed.
            let keyCode = Int(event.getIntegerValueField(.keyboardEventKeycode))
            let currentFlags = event.flags.rawValue
            let isDown = currentFlags > previousFlags
            previousFlags = currentFlags
            
            let eventType: SharedEventType = isDown ? .keyDown : .keyUp
            sharedEvent = SharedEvent(
                type: eventType,
                normalizedX: normalizedX,
                normalizedY: normalizedY,
                keyCode: keyCode,
                modifierFlags: currentFlags
            )
            
        default:
            return nil
        }
        
        dispatchEvent(sharedEvent)
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
    
    // If the tap is disabled by the system (e.g. timeout), re-enable it.
    // IMPORTANT: While disabled, keyboard events pass through to Mac apps
    // instead of being captured and forwarded to Linux.
    if type == .tapDisabledByTimeout {
        print("⚠️ [EventCapture] Event tap DISABLED by timeout — keyboard events were leaking to Mac! Re-enabling…")
        if let userInfo = userInfo {
            let capture = Unmanaged<EventCapture>.fromOpaque(userInfo).takeUnretainedValue()
            if let tap = capture.eventTap {
                CGEvent.tapEnable(tap: tap, enable: true)
                print("✅ [EventCapture] Event tap re-enabled after timeout.")
            }
        }
        return Unmanaged.passRetained(event)
    }
    
    if type == .tapDisabledByUserInput {
        print("⚠️ [EventCapture] Event tap DISABLED by user input — re-enabling…")
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

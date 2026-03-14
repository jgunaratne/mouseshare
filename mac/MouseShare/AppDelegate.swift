import Cocoa
import CoreGraphics

/// Main application delegate — wires together all components:
/// TCP server, event capture, event injection, edge detection, and status bar UI.
class AppDelegate: NSObject, NSApplicationDelegate {
    
    // MARK: - Components
    
    private let tcpManager = TCPManager()
    private let eventCapture = EventCapture()
    private let eventInjector = EventInjector()
    private let edgeDetector = ScreenEdgeDetector()
    private let statusBar = StatusBarController()
    
    /// Whether we are currently capturing and forwarding input to Linux.
    private var isControllingLinux = false
    
    /// Global mouse monitor for edge detection (runs even when other apps are in front).
    private var mouseMonitor: Any?
    
    /// Timer to check for USB-C network interface presence.
    private var interfaceCheckTimer: Timer?
    
    /// Whether the USB-C cable/interface has been detected.
    private var cableDetected = false
    
    /// Number of consecutive interface checks that failed to detect the USB-C interface.
    /// Teardown only occurs after 2+ misses to debounce transient dips.
    private var missedInterfaceChecks = 0
    
    // MARK: - App Lifecycle
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        // 1. Request Accessibility permission (prompts the user on first launch)
        requestAccessibilityPermission()
        
        // 2. Restore saved edge preference
        if let savedEdge = UserDefaults.standard.string(forKey: "selectedEdge"),
           let edge = ScreenEdge(rawValue: savedEdge) {
            selectedEdge = edge
        }
        
        // 3. Set up the status bar
        statusBar.setup()
        statusBar.selectedEdge = selectedEdge
        statusBar.onEdgeChanged = { [weak self] edge in
            self?.selectedEdge = edge
            print("⚙️ [MouseShare] Linux screen edge changed to: \(edge.rawValue)")
            // Notify connected companion(s) of the new edge.
            if let self = self, self.tcpManager.isConnected {
                let configEvent = SharedEvent(type: .edgeConfig, edge: edge.rawValue)
                self.tcpManager.send(configEvent)
            }
        }
        
        // 3. Set up TCP manager
        tcpManager.delegate = self
        tcpManager.onReturnControl = { [weak self] in
            // This runs on the TCP queue — sets the flag that the
            // event tap checks on the very next event.
            self?.eventCapture.shouldReturnControl = true
        }
        tcpManager.startListening()
        
        // 4. Set up edge detector
        edgeDetector.onEdgeReached = { [weak self] edge in
            self?.handleEdgeReached(edge)
        }
        
        // 5. Set up global mouse monitor for edge detection
        mouseMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.mouseMoved, .leftMouseDragged, .rightMouseDragged]) { [weak self] event in
            let location = NSEvent.mouseLocation
            self?.edgeDetector.check(mouseLocation: location)
        }
        
        // 6. Configure event capture to forward events over TCP
        eventCapture.onEvent = { [weak self] sharedEvent in
            guard let self = self else { return }
            
            if sharedEvent.type == .returnControl {
                self.returnControlToMac()
            } else {
                self.tcpManager.send(sharedEvent)
            }
        }
        
        // 7. Start periodic USB-C interface check (every 5 seconds)
        interfaceCheckTimer = Timer.scheduledTimer(withTimeInterval: 5.0, repeats: true) { [weak self] _ in
            self?.checkUSBInterface()
        }
        // Also check immediately on launch
        checkUSBInterface()
        
        print("🖱 [MouseShare] App launched and ready.")
    }
    
    func applicationWillTerminate(_ notification: Notification) {
        stopControllingLinux()
        tcpManager.stopListening()
        
        if let monitor = mouseMonitor {
            NSEvent.removeMonitor(monitor)
        }
        
        interfaceCheckTimer?.invalidate()
    }
    
    // MARK: - Accessibility
    
    private func requestAccessibilityPermission() {
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue(): true] as CFDictionary
        let trusted = AXIsProcessTrustedWithOptions(options)
        
        if trusted {
            print("✅ [MouseShare] Accessibility permission granted.")
        } else {
            print("⚠️ [MouseShare] Accessibility permission not yet granted.")
            print("   → The system dialog should appear. Grant permission and relaunch if needed.")
        }
    }
    
    /// Which Mac screen edge triggers the switch to Linux.
    /// Persisted in UserDefaults under "selectedEdge".
    private var selectedEdge: ScreenEdge = .right {
        didSet {
            UserDefaults.standard.set(selectedEdge.rawValue, forKey: "selectedEdge")
        }
    }
    
    // MARK: - Edge Detection → Start Controlling Linux
    
    private func handleEdgeReached(_ edge: ScreenEdge) {
        guard edge == selectedEdge else { return }
        guard tcpManager.isConnected, !isControllingLinux else { return }
        
        print("🔄 [MouseShare] Edge reached (\(edge.rawValue)) — switching control to Linux.")
        startControllingLinux()
    }
    
    private func startControllingLinux() {
        isControllingLinux = true
        
        // Pin the Mac cursor at its current location (the screen edge).
        let currentPos = NSEvent.mouseLocation
        guard let screen = NSScreen.main else { return }
        // NSEvent.mouseLocation is in Cocoa coords (origin bottom-left).
        // CGWarpMouseCursorPosition needs CG coords (origin top-left).
        let cgY = screen.frame.height - currentPos.y
        let pinPoint = CGPoint(x: currentPos.x, y: cgY)
        eventCapture.pinnedPosition = pinPoint
        eventCapture.selectedEdge = selectedEdge
        
        // Set the virtual cursor entry point based on which edge was crossed.
        let normalizedY = min(max(Double(cgY / screen.frame.height), 0), 1)
        let normalizedX = min(max(Double(currentPos.x / screen.frame.width), 0), 1)
        
        switch selectedEdge {
        case .right:
            // Companion is to the right → cursor enters from its left edge.
            eventCapture.virtualX = 0.01
            eventCapture.virtualY = normalizedY
        case .left:
            // Companion is to the left → cursor enters from its right edge.
            eventCapture.virtualX = 0.99
            eventCapture.virtualY = normalizedY
        case .top:
            // Companion is above → cursor enters from its bottom edge.
            eventCapture.virtualX = normalizedX
            eventCapture.virtualY = 0.99
        case .bottom:
            // Companion is below → cursor enters from its top edge.
            eventCapture.virtualX = normalizedX
            eventCapture.virtualY = 0.01
        }
        
        eventCapture.shouldReturnControl = false
        eventCapture.hideCursor = true
        eventCapture.start()
        statusBar.updateState(.controllingLinux)
    }
    
    private func stopControllingLinux() {
        eventCapture.hideCursor = false
        eventCapture.stop()
        isControllingLinux = false
    }
    
    private func returnControlToMac() {
        print("🔄 [MouseShare] Control returning to Mac.")
        stopControllingLinux()
        
        // Place the Mac cursor at the correct edge based on selectedEdge.
        if let screen = NSScreen.main {
            let w = screen.frame.width
            let h = screen.frame.height
            let cursorPoint: CGPoint
            
            switch selectedEdge {
            case .right:
                // Returning from the right — place cursor at right edge.
                cursorPoint = CGPoint(x: w - 1, y: eventCapture.virtualY * Double(h))
            case .left:
                // Returning from the left — place cursor at left edge.
                cursorPoint = CGPoint(x: 1, y: eventCapture.virtualY * Double(h))
            case .top:
                // Returning from above — place cursor at top edge (CG y=0).
                cursorPoint = CGPoint(x: eventCapture.virtualX * Double(w), y: 1)
            case .bottom:
                // Returning from below — place cursor at bottom edge.
                cursorPoint = CGPoint(x: eventCapture.virtualX * Double(w), y: h - 1)
            }
            
            CGWarpMouseCursorPosition(cursorPoint)
        }
        
        // Notify companion that control has returned to Mac.
        let returnEvent = SharedEvent(type: .returnControl)
        tcpManager.send(returnEvent)
        
        // Update status bar based on connection state.
        if tcpManager.isConnected {
            statusBar.updateState(.connected)
        } else if cableDetected {
            statusBar.updateState(.waitingForLinux)
        } else {
            statusBar.updateState(.cableNotDetected)
        }
    }
    
    // MARK: - USB-C Interface Detection
    
    /// Check if the USB-C network interface is present by scanning network interfaces
    /// for one with an address in the 192.168.100.x range.
    private func checkUSBInterface() {
        var detected = false
        
        var ifaddr: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&ifaddr) == 0, let firstAddr = ifaddr else { return }
        defer { freeifaddrs(ifaddr) }
        
        for ptr in sequence(first: firstAddr, next: { $0.pointee.ifa_next }) {
            let addr = ptr.pointee.ifa_addr.pointee
            guard addr.sa_family == UInt8(AF_INET) else { continue }
            
            // Convert to sockaddr_in to read the IP address
            var hostname = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            getnameinfo(
                ptr.pointee.ifa_addr,
                socklen_t(addr.sa_len),
                &hostname,
                socklen_t(hostname.count),
                nil,
                0,
                NI_NUMERICHOST
            )
            
            let address = String(cString: hostname)
            if address.hasPrefix("192.168.100.") {
                detected = true
                break
            }
        }
        
        let previouslyDetected = cableDetected
        cableDetected = detected
        
        if detected {
            // Cable found — reset miss counter.
            missedInterfaceChecks = 0
            
            if !previouslyDetected {
                // Cable was just plugged in — start (or restart) the TCP listener.
                print("🔌 [MouseShare] USB-C interface detected — starting listener.")
                tcpManager.stopListening()   // clean up any stale listener
                tcpManager.startListening()
                statusBar.updateState(.waitingForLinux)
            }
        } else if previouslyDetected {
            missedInterfaceChecks += 1
            
            if missedInterfaceChecks >= 2 {
                // Cable absent for 2+ consecutive checks — tear down.
                print("🔌 [MouseShare] USB-C interface lost (\(missedInterfaceChecks) missed checks) — stopping listener.")
                if isControllingLinux {
                    stopControllingLinux()
                }
                tcpManager.stopListening()
                statusBar.updateState(.cableNotDetected)
            } else {
                print("⚠️ [MouseShare] USB-C interface not found (\(missedInterfaceChecks)/2 missed checks) — waiting…")
            }
        }
    }
}

// MARK: - TCPManagerDelegate

extension AppDelegate: TCPManagerDelegate {
    func clientConnected() {
        print("🟢 [MouseShare] Companion client connected.")
        statusBar.updateState(.connected)
        // Inform the companion which edge the Mac has selected.
        let configEvent = SharedEvent(type: .edgeConfig, edge: selectedEdge.rawValue)
        tcpManager.send(configEvent)
    }
    
    func clientDisconnected() {
        print("🔴 [MouseShare] Linux client disconnected.")
        stopControllingLinux()
        
        if cableDetected {
            statusBar.updateState(.waitingForLinux)
        } else {
            statusBar.updateState(.cableNotDetected)
        }
    }
    
    func eventReceived(_ event: SharedEvent) {
        if event.type == .returnControl {
            // Set the flag directly — this is called on the TCP background
            // queue and the event tap will pick it up on the next event.
            eventCapture.shouldReturnControl = true
        } else {
            eventInjector.inject(event)
        }
    }
}

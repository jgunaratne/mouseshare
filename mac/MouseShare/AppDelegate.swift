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
    
    // MARK: - App Lifecycle
    
    func applicationDidFinishLaunching(_ notification: Notification) {
        // 1. Request Accessibility permission (prompts the user on first launch)
        requestAccessibilityPermission()
        
        // 2. Set up the status bar
        statusBar.setup()
        
        // 3. Set up TCP manager
        tcpManager.delegate = self
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
    
    // MARK: - Edge Detection → Start Controlling Linux
    
    private func handleEdgeReached(_ edge: ScreenEdge) {
        guard tcpManager.isConnected, !isControllingLinux else { return }
        
        print("🔄 [MouseShare] Edge reached (\(edge.rawValue)) — switching control to Linux.")
        startControllingLinux()
    }
    
    private func startControllingLinux() {
        isControllingLinux = true
        eventCapture.start()
        statusBar.updateState(.controllingLinux)
    }
    
    private func stopControllingLinux() {
        eventCapture.stop()
        isControllingLinux = false
    }
    
    private func returnControlToMac() {
        print("🔄 [MouseShare] Escape pressed — returning control to Mac.")
        stopControllingLinux()
        
        // Notify Linux that control has returned to Mac
        let returnEvent = SharedEvent(type: .returnControl)
        tcpManager.send(returnEvent)
        
        // Update status bar based on connection state
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
        
        // Only update status bar if not connected/controlling and state changed
        if !tcpManager.isConnected && !isControllingLinux {
            if detected && !previouslyDetected {
                print("🔌 [MouseShare] USB-C interface detected.")
                statusBar.updateState(.waitingForLinux)
            } else if !detected && previouslyDetected {
                print("🔌 [MouseShare] USB-C interface lost.")
                statusBar.updateState(.cableNotDetected)
            }
        }
    }
}

// MARK: - TCPManagerDelegate

extension AppDelegate: TCPManagerDelegate {
    func clientConnected() {
        print("🟢 [MouseShare] Linux client connected.")
        statusBar.updateState(.connected)
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
        // For future bidirectional support — inject received events locally
        eventInjector.inject(event)
    }
}

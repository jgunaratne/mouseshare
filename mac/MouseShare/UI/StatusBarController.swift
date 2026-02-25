import Cocoa

/// Connection states for the status bar UI.
enum ConnectionState: String {
    case cableNotDetected = "USB-C cable not detected"
    case waitingForLinux = "Waiting for Linux to connect…"
    case connected = "Connected to Linux"
    case controllingLinux = "Controlling Linux — press Esc to return"
}

/// Manages the menu bar status item, icon, and dropdown menu.
class StatusBarController {
    
    private var statusItem: NSStatusItem?
    private var menu: NSMenu?
    private var statusMenuItem: NSMenuItem?
    
    /// Menu items for each edge option — kept for updating checkmarks.
    private var edgeMenuItems: [ScreenEdge: NSMenuItem] = [:]
    
    private(set) var currentState: ConnectionState = .cableNotDetected
    
    /// The currently selected edge. Changing this updates the checkmarks.
    var selectedEdge: ScreenEdge = .right {
        didSet { updateEdgeCheckmarks() }
    }
    
    /// Called when the user picks a different edge from the menu.
    var onEdgeChanged: ((ScreenEdge) -> Void)?
    
    /// Set up the status bar item and menu.
    func setup() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        
        menu = NSMenu()
        
        // Status line
        statusMenuItem = NSMenuItem(title: currentState.rawValue, action: nil, keyEquivalent: "")
        statusMenuItem?.isEnabled = false
        menu?.addItem(statusMenuItem!)
        
        menu?.addItem(NSMenuItem.separator())
        
        // Hint lines
        let hint1 = NSMenuItem(title: "Push mouse to screen edge → control Linux", action: nil, keyEquivalent: "")
        hint1.isEnabled = false
        menu?.addItem(hint1)
        
        let hint2 = NSMenuItem(title: "Press Escape → return to Mac", action: nil, keyEquivalent: "")
        hint2.isEnabled = false
        menu?.addItem(hint2)
        
        menu?.addItem(NSMenuItem.separator())
        
        // Edge selection submenu
        let edgeSubmenu = NSMenu()
        for (edge, title) in [(ScreenEdge.right, "Right Edge"), (.left, "Left Edge"), (.top, "Top Edge"), (.bottom, "Bottom Edge")] {
            let item = NSMenuItem(title: title, action: #selector(edgeSelected(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = edge.rawValue
            edgeMenuItems[edge] = item
            edgeSubmenu.addItem(item)
        }
        let edgeMenuItem = NSMenuItem(title: "Linux Screen Edge", action: nil, keyEquivalent: "")
        edgeMenuItem.submenu = edgeSubmenu
        menu?.addItem(edgeMenuItem)
        
        menu?.addItem(NSMenuItem.separator())
        
        // IP address reference
        let ipItem = NSMenuItem(title: "Mac USB IP: 192.168.100.1", action: nil, keyEquivalent: "")
        ipItem.isEnabled = false
        menu?.addItem(ipItem)
        
        menu?.addItem(NSMenuItem.separator())
        
        // Quit
        let quitItem = NSMenuItem(title: "Quit MouseShare", action: #selector(quitApp), keyEquivalent: "q")
        quitItem.target = self
        menu?.addItem(quitItem)
        
        statusItem?.menu = menu
        
        updateState(.cableNotDetected)
        updateEdgeCheckmarks()
    }
    
    /// Update the connection state, icon, and status text.
    func updateState(_ state: ConnectionState) {
        currentState = state
        statusMenuItem?.title = state.rawValue
        
        // Update the icon using SF Symbols
        let symbolName: String
        switch state {
        case .cableNotDetected:
            symbolName = "cable.connector"
        case .waitingForLinux:
            symbolName = "cable.connector.horizontal"
        case .connected:
            symbolName = "cursorarrow.motionlines"
        case .controllingLinux:
            symbolName = "cursorarrow.motionlines"
        }
        
        if let button = statusItem?.button {
            let config = NSImage.SymbolConfiguration(pointSize: 16, weight: .medium)
            button.image = NSImage(systemSymbolName: symbolName, accessibilityDescription: "MouseShare")?
                .withSymbolConfiguration(config)
        }
    }
    
    private func updateEdgeCheckmarks() {
        for (edge, item) in edgeMenuItems {
            item.state = (edge == selectedEdge) ? .on : .off
        }
    }
    
    @objc private func edgeSelected(_ sender: NSMenuItem) {
        guard let rawValue = sender.representedObject as? String,
              let edge = ScreenEdge(rawValue: rawValue) else { return }
        selectedEdge = edge
        onEdgeChanged?(edge)
    }
    
    @objc private func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}

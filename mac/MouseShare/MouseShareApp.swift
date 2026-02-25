import SwiftUI

/// MouseShare entry point — a menu-bar-only macOS app (no main window, no Dock icon).
@main
struct MouseShareApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    
    var body: some Scene {
        // No visible windows — the app is entirely menu-bar driven.
        // Use Settings scene as a placeholder; the app has no settings window.
        Settings {
            EmptyView()
        }
    }
}

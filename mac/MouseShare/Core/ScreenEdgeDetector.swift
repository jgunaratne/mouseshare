import Cocoa

/// Which screen edge the cursor has reached.
enum ScreenEdge: String {
    case top, bottom, left, right
}

/// Detects when the mouse cursor reaches the outermost edge of the
/// combined multi-monitor layout (not individual monitor edges).
/// Includes a cooldown to prevent rapid re-firing and requires
/// the cursor to move away before the same edge fires again.
class ScreenEdgeDetector {
    
    /// Called when the cursor reaches a screen edge.
    var onEdgeReached: ((ScreenEdge) -> Void)?
    
    /// How close to the edge (in points) the cursor must be to trigger.
    private let edgeThreshold: CGFloat = 5.0
    
    /// Minimum time between triggers (seconds).
    private let cooldownInterval: TimeInterval = 0.5
    
    /// Last time an edge was triggered.
    private var lastTriggerTime: Date = .distantPast
    
    /// The last edge that was triggered — won't re-fire until cursor moves away.
    private var lastTriggeredEdge: ScreenEdge?
    
    /// Compute the bounding rectangle that encloses ALL connected screens.
    private var combinedFrame: CGRect {
        let screens = NSScreen.screens
        guard let first = screens.first else { return .zero }
        var union = first.frame
        for screen in screens.dropFirst() {
            union = union.union(screen.frame)
        }
        return union
    }
    
    /// Check whether the mouse is at a screen edge and fire the callback if so.
    /// Call this from a global mouse event monitor.
    func check(mouseLocation: NSPoint) {
        let frame = combinedFrame
        guard frame != .zero else { return }
        
        let detectedEdge: ScreenEdge?
        
        // NSScreen coordinates: origin is bottom-left of the primary display.
        // The combined frame covers the full extent of all monitors.
        if mouseLocation.x <= frame.minX + edgeThreshold {
            detectedEdge = .left
        } else if mouseLocation.x >= frame.maxX - edgeThreshold {
            detectedEdge = .right
        } else if mouseLocation.y <= frame.minY + edgeThreshold {
            detectedEdge = .bottom
        } else if mouseLocation.y >= frame.maxY - edgeThreshold {
            detectedEdge = .top
        } else {
            // Cursor is not at any edge — reset the last triggered edge
            lastTriggeredEdge = nil
            detectedEdge = nil
        }
        
        guard let edge = detectedEdge else { return }
        
        // Don't re-fire for the same edge until cursor has moved away and returned
        if edge == lastTriggeredEdge { return }
        
        // Enforce cooldown
        let now = Date()
        if now.timeIntervalSince(lastTriggerTime) < cooldownInterval { return }
        
        lastTriggerTime = now
        lastTriggeredEdge = edge
        onEdgeReached?(edge)
    }
}

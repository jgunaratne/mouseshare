import Foundation
import Network

/// Delegate callbacks for TCP connection state changes and received events.
protocol TCPManagerDelegate: AnyObject {
    func clientConnected()
    func clientDisconnected()
    func eventReceived(_ event: SharedEvent)
}

/// Manages a TCP server that listens for a single Linux client connection.
/// Messages are framed with a 4-byte big-endian length header followed by JSON payload.
class TCPManager {
    
    weak var delegate: TCPManagerDelegate?
    
    /// Called directly on the TCP queue (NOT main thread) when a
    /// returnControl event arrives — avoids main-thread starvation.
    var onReturnControl: (() -> Void)?
    
    /// Whether a client is currently connected.
    private(set) var isConnected: Bool = false
    
    /// The address to bind to (USB-C interface).
    private let host: NWEndpoint.Host = "192.168.100.1"
    
    /// The port to listen on.
    private let port: NWEndpoint.Port = 9876
    
    private var listener: NWListener?
    private var connection: NWConnection?
    private let queue = DispatchQueue(label: "com.mouseshare.tcp", qos: .userInteractive)
    
    /// Timer that sends heartbeat events to the connected client.
    private var heartbeatTimer: DispatchSourceTimer?
    
    /// Interval between heartbeat messages (seconds).
    private let heartbeatInterval: TimeInterval = 10
    
    // MARK: - Server Lifecycle
    
    /// Start listening for incoming connections on the USB-C interface.
    func startListening() {
        do {
            let params = NWParameters.tcp
            
            // Enable TCP keepalive at the OS level for faster dead-peer detection.
            if let tcpOptions = params.defaultProtocolStack.transportProtocol as? NWProtocolTCP.Options {
                tcpOptions.enableKeepalive = true
                tcpOptions.keepaliveIdle = 30     // seconds before first keepalive probe
                tcpOptions.keepaliveInterval = 10 // seconds between probes
                tcpOptions.keepaliveCount = 3     // probes before declaring dead
            }
            
            // Bind to the specific USB-C interface address
            params.requiredLocalEndpoint = NWEndpoint.hostPort(host: host, port: port)
            
            listener = try NWListener(using: params)
        } catch {
            print("❌ [TCPManager] Failed to create listener: \(error)")
            return
        }
        
        listener?.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                print("✅ [TCPManager] Listening on \(self?.host ?? "?"):\(self?.port ?? 0)")
            case .failed(let error):
                print("❌ [TCPManager] Listener failed: \(error)")
                self?.listener?.cancel()
                // Try again after a short delay
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    self?.startListening()
                }
            case .cancelled:
                print("⏹ [TCPManager] Listener cancelled.")
            default:
                break
            }
        }
        
        listener?.newConnectionHandler = { [weak self] newConnection in
            self?.handleNewConnection(newConnection)
        }
        
        listener?.start(queue: queue)
    }
    
    /// Stop the listener and disconnect any active client.
    func stopListening() {
        listener?.cancel()
        listener = nil
        disconnect()
    }
    
    // MARK: - Connection Handling
    
    private func handleNewConnection(_ newConnection: NWConnection) {
        // Only allow one connection at a time — cancel the old one
        if let existing = connection {
            print("⚠️ [TCPManager] New connection replacing existing one.")
            existing.cancel()
        }
        
        connection = newConnection
        
        newConnection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready:
                print("✅ [TCPManager] Client connected.")
                self?.isConnected = true
                self?.startHeartbeat()
                DispatchQueue.main.async {
                    self?.delegate?.clientConnected()
                }
                self?.receiveNextMessage()
                
            case .failed(let error):
                print("❌ [TCPManager] Connection failed: \(error)")
                self?.handleDisconnect()
                
            case .cancelled:
                self?.handleDisconnect()
                
            default:
                break
            }
        }
        
        newConnection.start(queue: queue)
    }
    
    private func disconnect() {
        stopHeartbeat()
        connection?.cancel()
        connection = nil
        if isConnected {
            isConnected = false
            DispatchQueue.main.async { [weak self] in
                self?.delegate?.clientDisconnected()
            }
        }
    }
    
    private func handleDisconnect() {
        print("📡 [TCPManager] Client disconnected.")
        stopHeartbeat()
        connection = nil
        isConnected = false
        DispatchQueue.main.async { [weak self] in
            self?.delegate?.clientDisconnected()
        }
    }
    
    // MARK: - Heartbeat
    
    /// Start sending periodic heartbeat events to keep the connection alive.
    private func startHeartbeat() {
        stopHeartbeat()
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + heartbeatInterval, repeating: heartbeatInterval)
        timer.setEventHandler { [weak self] in
            guard let self = self, self.isConnected else { return }
            let heartbeat = SharedEvent(type: .heartbeat)
            self.send(heartbeat)
        }
        heartbeatTimer = timer
        timer.resume()
    }
    
    /// Stop the heartbeat timer.
    private func stopHeartbeat() {
        heartbeatTimer?.cancel()
        heartbeatTimer = nil
    }
    
    // MARK: - Sending (Length-Prefixed JSON)
    
    /// Send an event to the connected Linux client.
    /// The message is framed as: [4-byte big-endian length][JSON payload]
    func send(_ event: SharedEvent) {
        guard let connection = connection, isConnected else { return }
        
        do {
            let jsonData = try JSONEncoder().encode(event)
            var length = UInt32(jsonData.count).bigEndian
            var frameData = Data(bytes: &length, count: 4)
            frameData.append(jsonData)
            
            connection.send(content: frameData, completion: .contentProcessed { error in
                if let error = error {
                    print("⚠️ [TCPManager] Send error: \(error)")
                }
            })
        } catch {
            print("❌ [TCPManager] Failed to encode event: \(error)")
        }
    }
    
    // MARK: - Receiving (Length-Prefixed JSON)
    
    /// Read the next length-prefixed message from the connection.
    private func receiveNextMessage() {
        guard let connection = connection else { return }
        
        // First, read the 4-byte length header
        connection.receive(minimumIncompleteLength: 4, maximumLength: 4) { [weak self] data, _, isComplete, error in
            if let error = error {
                print("❌ [TCPManager] Receive header error: \(error)")
                self?.handleDisconnect()
                return
            }
            
            if isComplete {
                self?.handleDisconnect()
                return
            }
            
            guard let headerData = data, headerData.count == 4 else {
                self?.handleDisconnect()
                return
            }
            
            // Parse the 4-byte big-endian length
            let length = headerData.withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
            
            guard length > 0, length < 1_000_000 else {
                print("⚠️ [TCPManager] Invalid message length: \(length)")
                self?.handleDisconnect()
                return
            }
            
            // Now read exactly `length` bytes of payload
            self?.receivePayload(length: Int(length))
        }
    }
    
    private func receivePayload(length: Int) {
        guard let connection = connection else { return }
        
        connection.receive(minimumIncompleteLength: length, maximumLength: length) { [weak self] data, _, isComplete, error in
            if let error = error {
                print("❌ [TCPManager] Receive payload error: \(error)")
                self?.handleDisconnect()
                return
            }
            
            if isComplete {
                self?.handleDisconnect()
                return
            }
            
            guard let payloadData = data, payloadData.count == length else {
                print("⚠️ [TCPManager] Incomplete payload.")
                self?.handleDisconnect()
                return
            }
            
            // Decode the JSON payload
            do {
                let event = try JSONDecoder().decode(SharedEvent.self, from: payloadData)
                if event.type == .returnControl {
                    // Fire directly on TCP queue — bypasses blocked main thread.
                    self?.onReturnControl?()
                } else {
                    DispatchQueue.main.async {
                        self?.delegate?.eventReceived(event)
                    }
                }
            } catch {
                print("⚠️ [TCPManager] Failed to decode event: \(error)")
            }
            
            // Loop: wait for the next message
            self?.receiveNextMessage()
        }
    }
}

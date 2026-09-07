import SwiftUI
import UIKit
import Combine

@MainActor final class PhoneModel: ObservableObject {
    static let shared = PhoneModel()
    @Published var queued = 0
    @Published var incomplete = 0
    @Published var paused = true
    @Published var available = false
    @Published var pairedHost = ""
    @Published var detail = "Checking the local queue…"
    @Published var busy = false
    @Published var pending: Pairing?
    @Published var pairingConfirmation = false
    func refresh() {
        DispatchQueue.global(qos: .utility).async {
            do {
                let store = try PhoneStore.open(), status = try store.status()
                let raw = try store.setting("pairing")
                let host = try raw.map { try JSONDecoder().decode(Pairing.self, from: Data($0.utf8)).host } ?? ""
                DispatchQueue.main.async {
                    self.available = true
                    self.queued = status["queued"] as? Int ?? 0; self.incomplete = status["incomplete"] as? Int ?? 0
                    self.paused = status["paused"] as? Bool ?? false; self.pairedHost = host
                    let error = status["lastError"] as? String ?? ""
                    self.detail = error.isEmpty ? (status["lastSync"] as? String).flatMap { $0.isEmpty ? nil : "Last delivery: " + $0 } ?? "Snapshots stay here until your Mac confirms receipt." : error
                }
            } catch { DispatchQueue.main.async { self.available = false; self.detail = error.localizedDescription } }
        }
    }
    func receive(_ url: URL) {
        do { pending = try Pairing(url: url); pairingConfirmation = true }
        catch { detail = error.localizedDescription }
    }
    func pair() {
        guard let pairing = pending else { return }
        do {
            let store = try PhoneStore.open()
            try store.set("pairing", String(decoding: JSONEncoder().encode(pairing), as: UTF8.self))
            try store.set("error", ""); pending = nil; refresh(); sync()
        } catch { detail = error.localizedDescription }
    }
    func setPaused(_ paused: Bool) {
        do { try PhoneStore.open().set("paused", paused ? "true" : "false"); self.paused = paused }
        catch { detail = error.localizedDescription }
    }
    func sync() {
        if busy { return }; busy = true
        DispatchQueue.global(qos: .utility).async {
            do {
                let store = try PhoneStore.open()
                // Foreground delivery is bounded. Remaining records stay queued.
                let deadline = Date().addingTimeInterval(90)
                while Date() < deadline {
                    if !(try PhoneStore.sync(store)) { break }
                }
            } catch {
                if let store = try? PhoneStore.open() { try? store.set("error", error.localizedDescription) }
            }
            DispatchQueue.main.async { self.busy = false; self.refresh() }
        }
    }
}

struct SeenScreen: View {
    @StateObject private var model = PhoneModel.shared
    @Environment(\.scenePhase) private var phase
    private let ink = Color(red: 0.16, green: 0.25, blue: 0.20)
    private let paper = Color(red: 0.965, green: 0.953, blue: 0.914)
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                HStack(alignment: .center, spacing: 14) {
                    Image(systemName: "eye").font(.system(size: 42, weight: .light)).accessibilityHidden(true)
                    Text("Seen").font(.custom("Georgia", size: 52, relativeTo: .largeTitle))
                }.padding(.top, 30)
                Text("What you browse.\nKept close.").font(.custom("Georgia", size: 28, relativeTo: .title2)).fixedSize(horizontal: false, vertical: true)
                VStack(alignment: .leading, spacing: 12) {
                    Text("ON THIS IPHONE").font(.custom("AvenirNext-DemiBold", size: 11)).tracking(2)
                    HStack(alignment: .firstTextBaseline) {
                        Text(model.queued.formatted()).font(.custom("Georgia", size: 54, relativeTo: .largeTitle)).monospacedDigit()
                        Text("queued snapshots").font(.custom("AvenirNext-Regular", size: 15))
                    }
                    if model.incomplete > 0 { Text("\(model.incomplete) incomplete transfers retained").font(.caption) }
                    Toggle("Capture Safari", isOn: Binding(get: { !model.paused }, set: { model.setPaused(!$0) })).tint(ink).disabled(!model.available)
                    Text("Enable Seen for All Websites in Settings → Apps → Safari → Extensions. Keep ‘Allow in Private Browsing’ off.").font(.custom("AvenirNext-Regular", size: 13)).fixedSize(horizontal: false, vertical: true)
                }.padding(24).background(ink.opacity(0.055), in: RoundedRectangle(cornerRadius: 18))
                VStack(alignment: .leading, spacing: 12) {
                    Text(model.pairedHost.isEmpty ? "Pair your Mac" : "Your Mac · " + model.pairedHost).font(.custom("Georgia", size: 23, relativeTo: .title3))
                    Text(model.pairedHost.isEmpty ? "Start the Seen receiver on your Mac. Open its pairing QR, then scan it with your iPhone Camera." : "Delivery stays on your local network. Your Mac’s receiver must be running.").font(.custom("AvenirNext-Regular", size: 15)).fixedSize(horizontal: false, vertical: true)
                    Button { model.sync() } label: {
                        HStack { if model.busy { ProgressView().tint(paper) }; Text(model.busy ? "Delivering…" : "Sync now"); Spacer(); Image(systemName: "arrow.up.right") }.padding(16)
                    }.buttonStyle(.plain).background(ink, in: RoundedRectangle(cornerRadius: 12)).foregroundStyle(paper).disabled(model.busy || model.pairedHost.isEmpty).opacity(model.pairedHost.isEmpty ? 0.5 : 1)
                    Text(model.detail).font(.custom("AvenirNext-Regular", size: 12)).foregroundStyle(ink.opacity(0.7)).accessibilityIdentifier("deliveryStatus")
                }
                Text("DOM ONLY   ·   NO CLOUD ARCHIVE\nNO WEBSITE REQUESTS   ·   NO PRIVATE TABS").font(.custom("AvenirNext-DemiBold", size: 10)).tracking(1).lineSpacing(6).foregroundStyle(ink.opacity(0.6))
            }.padding(28).frame(maxWidth: 600)
        }.frame(maxWidth: .infinity).background(paper).foregroundStyle(ink)
        .alert("Pair with \(model.pending?.host ?? "your Mac")?", isPresented: $model.pairingConfirmation) {
            Button("Pair Mac") { model.pair() }; Button("Cancel", role: .cancel) { model.pending = nil }
        } message: { Text("This Mac will receive your saved browsing DOM, including private page content. Only pair using the QR displayed by your own Seen receiver.") }
        .task { model.refresh() }
        .onChange(of: phase) { _, phase in if phase == .active { model.refresh(); model.sync() } }
    }
}

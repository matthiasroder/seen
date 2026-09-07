//
//  SceneDelegate.swift
//  SeenMobile
//
//  Created by Matthias Röder on 31.08.26.
//

import UIKit
import SwiftUI

class SceneDelegate: UIResponder, UIWindowSceneDelegate {

    var window: UIWindow?

    func scene(_ scene: UIScene, willConnectTo session: UISceneSession, options connectionOptions: UIScene.ConnectionOptions) {
        guard let scene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: scene)
        window.rootViewController = UIHostingController(rootView: SeenScreen())
        self.window = window; window.makeKeyAndVisible()
        if let url = connectionOptions.urlContexts.first?.url { PhoneModel.shared.receive(url) }
    }

    func scene(_ scene: UIScene, openURLContexts URLContexts: Set<UIOpenURLContext>) {
        if let url = URLContexts.first?.url { PhoneModel.shared.receive(url) }
    }

}

#!/usr/bin/env swift
import Foundation
import NaturalLanguage

struct Input: Decodable {
    let id: Int
    let text: String
}

struct Output: Encodable {
    let id: Int
    let model: String
    let dimensions: Int
    let vector: [Double]
}

let forced = CommandLine.arguments.dropFirst().first
let encoder = JSONEncoder()

func language(for text: String) -> NLLanguage {
    if forced == "de" { return .german }
    if forced == "en" { return .english }
    let recognizer = NLLanguageRecognizer()
    recognizer.processString(text)
    return recognizer.dominantLanguage == .german ? .german : .english
}

while let line = readLine() {
    guard let data = line.data(using: .utf8),
          let input = try? JSONDecoder().decode(Input.self, from: data) else {
        fputs("Invalid embedding input.\n", stderr)
        exit(2)
    }
    let selected = language(for: input.text)
    guard let embedding = NLEmbedding.sentenceEmbedding(for: selected),
          let vector = embedding.vector(for: input.text) else {
        fputs("Apple Natural Language could not embed text.\n", stderr)
        exit(3)
    }
    let result = Output(id: input.id, model: "apple-nl-" + selected.rawValue,
                        dimensions: embedding.dimension, vector: vector)
    FileHandle.standardOutput.write(try encoder.encode(result))
    FileHandle.standardOutput.write(Data([0x0a]))
}

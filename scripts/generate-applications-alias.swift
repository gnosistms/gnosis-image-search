#!/usr/bin/env swift

import AppKit
import Foundation

guard CommandLine.arguments.count == 3 else {
    fputs("Usage: generate-applications-alias.swift ICON_PNG OUTPUT_ALIAS\n", stderr)
    exit(2)
}

let iconPath = URL(fileURLWithPath: CommandLine.arguments[1]).standardized.path
let outputPath = URL(fileURLWithPath: CommandLine.arguments[2]).standardized.path
let outputDirectory = URL(fileURLWithPath: outputPath).deletingLastPathComponent().path

try FileManager.default.createDirectory(
    atPath: outputDirectory,
    withIntermediateDirectories: true
)
if FileManager.default.fileExists(atPath: outputPath) {
    try FileManager.default.removeItem(atPath: outputPath)
}

let destination = URL(fileURLWithPath: "/Applications", isDirectory: true)
let aliasData = try destination.bookmarkData(
    options: .suitableForBookmarkFile,
    includingResourceValuesForKeys: nil,
    relativeTo: nil
)
try URL.writeBookmarkData(aliasData, to: URL(fileURLWithPath: outputPath))

guard let icon = NSImage(contentsOfFile: iconPath) else {
    fputs("Could not load folder icon at \(iconPath).\n", stderr)
    exit(1)
}
guard NSWorkspace.shared.setIcon(icon, forFile: outputPath, options: []) else {
    fputs("Could not apply the custom icon to \(outputPath).\n", stderr)
    exit(1)
}

print("Wrote \(outputPath)")

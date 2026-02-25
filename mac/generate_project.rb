#!/usr/bin/env ruby
# Generate the MouseShare.xcodeproj using the xcodeproj gem.
# Run: ruby generate_project.rb

require 'xcodeproj'

project_path = File.join(__dir__, 'MouseShare.xcodeproj')
project = Xcodeproj::Project.new(project_path)

# --- Build Configuration ---
project.build_configurations.each do |config|
  config.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = 'com.junius.mouseshare'
  config.build_settings['MACOSX_DEPLOYMENT_TARGET'] = '12.0'
  config.build_settings['SWIFT_VERSION'] = '5.0'
  config.build_settings['INFOPLIST_FILE'] = 'MouseShare/Info.plist'
  config.build_settings['CODE_SIGN_ENTITLEMENTS'] = 'MouseShare/MouseShare.entitlements'
  config.build_settings['ENABLE_APP_SANDBOX'] = 'NO'
  config.build_settings['CODE_SIGN_STYLE'] = 'Automatic'
  config.build_settings['PRODUCT_NAME'] = 'MouseShare'
  config.build_settings['COMBINE_HIDPI_IMAGES'] = 'YES'
  config.build_settings['GENERATE_INFOPLIST_FILE'] = 'NO'
  config.build_settings['CURRENT_PROJECT_VERSION'] = '1'
  config.build_settings['MARKETING_VERSION'] = '1.0'
end

# --- Native Target ---
target = project.new_target(:application, 'MouseShare', :osx, '12.0')

target.build_configurations.each do |config|
  config.build_settings['PRODUCT_BUNDLE_IDENTIFIER'] = 'com.junius.mouseshare'
  config.build_settings['MACOSX_DEPLOYMENT_TARGET'] = '12.0'
  config.build_settings['SWIFT_VERSION'] = '5.0'
  config.build_settings['INFOPLIST_FILE'] = 'MouseShare/Info.plist'
  config.build_settings['CODE_SIGN_ENTITLEMENTS'] = 'MouseShare/MouseShare.entitlements'
  config.build_settings['ENABLE_APP_SANDBOX'] = 'NO'
  config.build_settings['CODE_SIGN_STYLE'] = 'Automatic'
  config.build_settings['PRODUCT_NAME'] = 'MouseShare'
  config.build_settings['COMBINE_HIDPI_IMAGES'] = 'YES'
  config.build_settings['GENERATE_INFOPLIST_FILE'] = 'NO'
  config.build_settings['CURRENT_PROJECT_VERSION'] = '1'
  config.build_settings['MARKETING_VERSION'] = '1.0'
  config.build_settings['LD_RUNPATH_SEARCH_PATHS'] = '$(inherited) @executable_path/../Frameworks'
end

# --- Groups ---
main_group = project.main_group
mouseshare_group = main_group.new_group('MouseShare', 'MouseShare')
core_group = mouseshare_group.new_group('Core', 'Core')
network_group = mouseshare_group.new_group('Network', 'Network')
ui_group = mouseshare_group.new_group('UI', 'UI')

# --- Source Files ---
source_files = {
  mouseshare_group => [
    'MouseShare/MouseShareApp.swift',
    'MouseShare/AppDelegate.swift',
  ],
  core_group => [
    'MouseShare/Core/SharedEvent.swift',
    'MouseShare/Core/EventCapture.swift',
    'MouseShare/Core/EventInjector.swift',
    'MouseShare/Core/ScreenEdgeDetector.swift',
  ],
  network_group => [
    'MouseShare/Network/TCPManager.swift',
  ],
  ui_group => [
    'MouseShare/UI/StatusBarController.swift',
  ],
}

source_files.each do |group, paths|
  paths.each do |path|
    file_ref = group.new_reference(File.basename(path))
    file_ref.set_path(File.basename(path))
    target.source_build_phase.add_file_reference(file_ref)
  end
end

# --- Resource/Config Files (not compiled, just referenced) ---
info_ref = mouseshare_group.new_reference('Info.plist')
entitlements_ref = mouseshare_group.new_reference('MouseShare.entitlements')

# --- Frameworks ---
# AppKit, CoreGraphics, Network are all implicit for macOS apps via SwiftUI
# No need to explicitly link them in modern Xcode

project.save

puts "✅ MouseShare.xcodeproj generated successfully!"

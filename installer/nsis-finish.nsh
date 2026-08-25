!macro customInit
  ${if} ${FileExists} "$LOCALAPPDATA\GnosisImages\Update.exe"
    DetailPrint "Removing the previous Gnosis Images installer"
    ExecWait '"$LOCALAPPDATA\GnosisImages\Update.exe" --uninstall -s'
  ${endif}
!macroend

!macro customFinishPage
  Function StartGnosisImages
    ${if} ${isUpdated}
      StrCpy $1 "--updated"
    ${else}
      StrCpy $1 ""
    ${endif}
    ${StdUtils.ExecShellAsUser} $0 "$launchLink" "open" "$1"
  FunctionEnd

  Function CreateGnosisDesktopShortcut
    CreateShortCut "$DESKTOP\${SHORTCUT_NAME}.lnk" "$appExe" "" "$appExe" 0 "" "" "${APP_DESCRIPTION}"
    ClearErrors
    WinShell::SetLnkAUMI "$DESKTOP\${SHORTCUT_NAME}.lnk" "${APP_ID}"
    System::Call 'shell32::SHChangeNotify(i, i, i, i) v (0x08000000, 0, 0, 0)'
  FunctionEnd

  !define MUI_FINISHPAGE_TEXT "Gnosis Images has been installed on your computer."
  !define MUI_FINISHPAGE_RUN
  !define MUI_FINISHPAGE_RUN_TEXT "Run Gnosis Images"
  !define MUI_FINISHPAGE_RUN_FUNCTION "StartGnosisImages"
  !define MUI_FINISHPAGE_SHOWREADME
  !define MUI_FINISHPAGE_SHOWREADME_TEXT "Create a desktop shortcut"
  !define MUI_FINISHPAGE_SHOWREADME_FUNCTION "CreateGnosisDesktopShortcut"
  !insertmacro MUI_PAGE_FINISH
!macroend

!macro customUnInstall
  ${ifNot} ${isKeepShortcuts}
    WinShell::UninstShortcut "$oldDesktopLink"
    Delete "$oldDesktopLink"
  ${endif}
!macroend

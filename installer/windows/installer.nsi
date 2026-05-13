; ScrollReader Windows Installer
; Built with NSIS (https://nsis.sourceforge.io)

Unicode True

!define APP_NAME "ScrollReader"
!define APP_VERSION "${VERSION}"
!define APP_PUBLISHER "ampersandwichmaker"
!define APP_URL "https://github.com/ampersandwichmaker/scrollreader"
!define APP_EXE "ScrollReader.exe"
!define INSTALL_DIR "$PROGRAMFILES64\${APP_NAME}"
!define UNINSTALLER "Uninstall.exe"

; Output filename
OutFile "ScrollReader_Setup_${VERSION}.exe"

; Default install directory
InstallDir "${INSTALL_DIR}"

; Registry key for install path (used by uninstaller)
InstallDirRegKey HKLM "Software\${APP_NAME}" "InstallDir"

; Require admin rights
RequestExecutionLevel admin

; Modern UI
!include "MUI2.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "..\..\assets\icon.ico"
!define MUI_UNICON "..\..\assets\icon.ico"
!define MUI_WELCOMEPAGE_TITLE "Welcome to ScrollReader ${VERSION}"
!define MUI_WELCOMEPAGE_TEXT "A focused, keyboard-driven PDF reader.$\r$\n$\r$\nClick Next to continue."
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Launch ScrollReader"

; Pages
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; Language
!insertmacro MUI_LANGUAGE "English"

; Version info shown in exe properties
VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName"      "${APP_NAME}"
VIAddVersionKey "ProductVersion"   "${APP_VERSION}"
VIAddVersionKey "CompanyName"      "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription"  "ScrollReader Installer"
VIAddVersionKey "FileVersion"      "${APP_VERSION}"
VIAddVersionKey "LegalCopyright"   "MIT License"

; ─── Install ────────────────────────────────────────────────────────────────

Section "ScrollReader" SecMain
  SectionIn RO

  SetOutPath "$INSTDIR"

  File "..\..\dist\ScrollReader.exe"

  WriteRegStr HKLM "Software\${APP_NAME}" "InstallDir" "$INSTDIR"

  WriteUninstaller "$INSTDIR\${UNINSTALLER}"

  WriteRegStr HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "Publisher" "${APP_PUBLISHER}"
  WriteRegStr HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "URLInfoAbout" "${APP_URL}"
  WriteRegStr HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "UninstallString" "$INSTDIR\${UNINSTALLER}"
  WriteRegStr HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "InstallLocation" "$INSTDIR"
  WriteRegDWORD HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "NoModify" 1
  WriteRegDWORD HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" \
    "NoRepair" 1

  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" \
    "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk" \
    "$INSTDIR\${UNINSTALLER}"

  CreateShortcut "$DESKTOP\${APP_NAME}.lnk" \
    "$INSTDIR\${APP_EXE}" "" "$INSTDIR\${APP_EXE}"

SectionEnd

; ─── Uninstall ───────────────────────────────────────────────────────────────

Section "Uninstall"

  Delete "$INSTDIR\${APP_EXE}"
  Delete "$INSTDIR\${UNINSTALLER}"
  RMDir  "$INSTDIR"

  Delete "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk"
  Delete "$SMPROGRAMS\${APP_NAME}\Uninstall ${APP_NAME}.lnk"
  RMDir  "$SMPROGRAMS\${APP_NAME}"

  Delete "$DESKTOP\${APP_NAME}.lnk"

  DeleteRegKey HKLM "Software\${APP_NAME}"
  DeleteRegKey HKLM \
    "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

SectionEnd

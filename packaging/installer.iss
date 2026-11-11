; ============================================================================
;  Inno Setup 安装包脚本（中英双语）
;
;  编译（需要先装 Inno Setup 6）：
;      python -m PyInstaller packaging\PDFTranslator.spec --noconfirm
;      "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
;  几个刻意的设定：
;
;  1) PrivilegesRequired=lowest
;     默认按当前用户安装，装到 %LOCALAPPDATA%\Programs，不弹 UAC。
;     体验版发给别人，最怕的就是卡在「请输入管理员密码」那一步。
;     管理员用户可以自己在向导里勾选「为所有用户安装」，那时才装到
;     Program Files，会正常请求提权。
;
;  2) 不打包 data\config.json
;     那是开发者自己的 API key。安装包里的默认配置由程序运行时生成，
;     API 默认是空的，用户自己填。
;
;  3) 卸载时问一句要不要删配置
;     卸载完直接删掉用户的术语库和缓存，是很多人骂软件的原因。
;
;  4) 双语向导，默认跟随系统语言
;     中文语言文件是本目录下的 ChineseSimplified.isl —— Inno 官方不带简体中文，
;     里面只写了向导上真正会看到的那几句，没写的自动落回英文 Default.isl。
;     不引第三方整份翻译，是因为那种文件几百条，官方升级一次就可能对不上，
;     到时候界面上蹦出半句没翻的话反而更难看。
; ============================================================================

#define AppName "PDF 图纸翻译"
#define AppNameEn "PDF Drawing Translator"
#define AppVer "1.0.2"
#define AppExe "PDF图纸翻译.exe"
#define AppPublisher "Walter"

[Setup]
AppId={{E07108F2-9CFA-4C91-A748-948BDE9016E8}
; AppName 只能有一个，这里是双语的：英文用户看得出这是什么，中文用户认得出来。
; 安装目录名用纯 ASCII 的 DrawingTranslator，避免中文路径给解压/杀软添麻烦。
AppName={#AppName} (PDF Drawing Translator)
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\PDFDrawingTranslator
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=PDFTranslator_Setup_{#AppVer}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
; 让安装包启动时用系统语言挑向导语言（中文系统进中文向导，其余进英文）
LanguageDetectionMethod=uilanguage

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zh"; MessagesFile: "ChineseSimplified.isl"

; 任务、运行、卸载提示这些散在别处的文案，用 CustomMessages 按语言给两份。
; 直接写死中文的话，英文用户会在「附加任务」里看到一行中文。
[CustomMessages]
en.CreateDesktopIcon=Create a desktop shortcut
zh.CreateDesktopIcon=创建桌面快捷方式
en.AdditionalTasks=Additional tasks:
zh.AdditionalTasks=附加任务：
en.RunAfterInstall=Launch {#AppNameEn}
zh.RunAfterInstall=立即运行 {#AppName}
en.SamplesFolder=Sample drawings
zh.SamplesFolder=示例图纸
en.ShortcutGuide=User guide
zh.ShortcutGuide=使用说明
en.KeepDataPrompt=Also delete your settings and translation cache?%n%nKeeping them means a reinstall picks up where you left off (your API key and glossary live there).%n%nChoose Yes to delete:
zh.KeepDataPrompt=是否一并删除配置和翻译缓存？%n%n留着的话，以后重装还能接着用（里面有你自己填的 API key 和术语库）。%n%n选「是」会删掉这些：
en.KeepDataTitle=Uninstall
zh.KeepDataTitle=卸载

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalTasks}"

[Files]
Source: "..\dist\PDF图纸翻译\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 两版说明都放进安装目录：程序本身是中英双语，用户可能想换一版看
Source: "..\使用说明.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER-GUIDE.md"; DestDir: "{app}"; DestName: "User Guide (EN).md"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\samples\*.pdf"; DestDir: "{app}\{cm:SamplesFolder}"; Flags: ignoreversion

[Icons]
; 快捷方式名按安装语言给：英文用户桌面上出现「PDF Drawing Translator」，
; 中文用户看到「PDF 图纸翻译」。指向的都是同一个 exe。
; exe 文件名保持中文不变 —— 改它会让老版本的快捷方式指向一个不存在的文件，
; 而且安装目录里会新旧两个 exe 并存。用户在快捷方式和任务栏里看到的已是本地化名字，
; 中文文件名只在「打开文件所在位置」时才会遇到，不值得为它冒升级风险。
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Languages: zh
Name: "{group}\{#AppNameEn}"; Filename: "{app}\{#AppExe}"; Languages: en
; 开始菜单里的说明，按当前安装语言指向对应那一版
Name: "{group}\使用说明"; Filename: "{app}\使用说明.md"; Languages: zh
Name: "{group}\User guide"; Filename: "{app}\User Guide (EN).md"; Languages: en
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"; Languages: zh
Name: "{group}\Uninstall {#AppNameEn}"; Filename: "{uninstallexe}"; Languages: en
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; Languages: zh
Name: "{autodesktop}\{#AppNameEn}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon; Languages: en

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:RunAfterInstall}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// 卸载收尾时问一句：配置和翻译缓存要不要一起删。
// 直接删掉用户的术语库，是最容易挨骂的做法；直接留着，硬盘里又会悄悄堆几百兆。
//
// 要问两个目录：一个是现在的 %APPDATA%\PDFDrawingTranslator，
// 一个是老版本的 %APPDATA%\PDF图纸翻译。程序启动时会把老的搬进新的，
// 但搬不动（文件被占用、权限不足）时老目录还留在原地，卸载不能把它落下，
// 否则用户选「是」删干净了，硬盘里还藏着一份带 API key 的旧配置。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, LegacyDir, Targets: String;
  Found: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\PDFDrawingTranslator');
    LegacyDir := ExpandConstant('{userappdata}\PDF图纸翻译');
    Targets := '';
    Found := False;
    if DirExists(DataDir) then
    begin
      Targets := DataDir;
      Found := True;
    end;
    if DirExists(LegacyDir) then
    begin
      if Found then
        Targets := Targets + #13#10 + LegacyDir
      else
        Targets := LegacyDir;
      Found := True;
    end;
    if Found then
    begin
      if MsgBox(ExpandConstant('{cm:KeepDataPrompt}') + #13#10 + Targets,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        if DirExists(DataDir) then
          DelTree(DataDir, True, True, True);
        if DirExists(LegacyDir) then
          DelTree(LegacyDir, True, True, True);
      end;
    end;
  end;
end;

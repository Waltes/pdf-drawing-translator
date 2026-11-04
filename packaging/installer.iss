; ============================================================================
;  Inno Setup 安装包脚本
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
; ============================================================================

#define AppName "PDF 图纸翻译"
#define AppVer "1.0.0"
#define AppExe "PDF图纸翻译.exe"
#define AppPublisher "Walter"

[Setup]
AppId={{E07108F2-9CFA-4C91-A748-948BDE9016E8}
AppName={#AppName}
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\PDF图纸翻译
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=PDFTranslator_Setup_{#AppVer}
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

; 只把向导里真正会看到的文案换成中文。
; 不做完整的 isl 翻译：官方包不带简体中文，自己补一份几百条的文件，
; 漏一条就会在界面上蹦出半句英文，反而更难看。
[Messages]
WelcomeLabel1=欢迎安装 [name]
WelcomeLabel2=即将把 [name] 装到你的电脑上。%n%n它可以把工程图纸 PDF 在中文和英文之间对译，图形和版式不动，英制尺寸自动换算成公制。%n%n关闭其它正在运行的程序，然后点「下一步」继续。
WizardSelectDir=选择安装位置
SelectDirDesc=装到哪里？
SelectDirLabel3=安装程序会把 [name] 装到下面的文件夹里。
SelectDirBrowseLabel=如果想换一个位置，点「浏览」。装完点「下一步」。
WizardReady=准备安装
ReadyLabel1=现在可以开始安装了。
ReadyLabel2a=点「安装」开始，或者点「上一步」回去改设置。
ReadyLabel2b=点「安装」开始。
WizardInstalling=正在安装
FinishedHeadingLabel=安装完成
FinishedLabelNoIcons=装好了。双击桌面上的「PDF 图纸翻译」就能用。%n%n第一次打开建议先拖一份 PDF 进去试，或者用安装目录下「示例图纸」里的那份。
FinishedLabel=装好了。双击桌面上的「PDF 图纸翻译」就能用。%n%n第一次打开建议先拖一份 PDF 进去试，或者用安装目录下「示例图纸」里的那份。
ClickNext=点「下一步」继续。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonFinish=完成(&F)
ButtonCancel=取消
ButtonBrowse=浏览(&R)...
ButtonYes=是(&Y)
ButtonNo=否(&N)
ExitSetupTitle=退出安装
ExitSetupMessage=安装还没完成。现在退出，程序不会装上。%n%n确定要退出吗？
ConfirmUninstall=确定要卸载 %1 吗？

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："

[Files]
Source: "..\dist\PDF图纸翻译\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\使用说明.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\samples\*.pdf"; DestDir: "{app}\示例图纸"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.md"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// 卸载收尾时问一句：配置和翻译缓存要不要一起删。
// 直接删掉用户的术语库，是最容易挨骂的做法；直接留着，硬盘里又会悄悄堆几百兆。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\PDF图纸翻译');
    if DirExists(DataDir) then
    begin
      if MsgBox('是否一并删除配置和翻译缓存？' + #13#10 + #13#10 +
                '留着的话，以后重装还能接着用（里面有你自己填的 API key 和术语库）。' + #13#10 +
                '选「是」会把这个文件夹整个删掉：' + #13#10 + DataDir,
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;

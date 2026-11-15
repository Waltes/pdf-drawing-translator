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
;     中文语言文件是本目录下的 ChineseSimplified.isl。Inno 官方不带简体中文，
;     这里拿官方 6.5 的简体中文翻译（official_zh_6.5.isl）打底，再用本项目的
;     说法覆盖（zh_overrides.isl），由 merge_zh_isl.py 生成。
;     早先只写了几十条自定义消息，没写的会自动落回英文，结果向导中英混杂 ——
;     标准消息有两百多条，漏一条就是一句英文冒出来，所以改成整份官方翻译打底。
;     重新生成：python packaging/merge_zh_isl.py
;  5) 品牌/版本/安装目录全部来自 edition_defs.iss
;     这个文件由 packaging\build_edition.py 按 editions\<id>.json 生成，
;     里面就是几行 #define。不要在本文件里写死任何品牌名 ——
;     通用版和定制版的差别只有这几行，写死等于要把整个脚本复制一份。
;     手工编译前先跑一次：
;         python packaging\build_edition.py --defs-only
;     （不带参数就是当前 edition.json 那一版）
; ============================================================================

#include "edition_defs.iss"

[Setup]
; AppId 两版必须不同：相同的话装定制版会顶掉通用版，卸载时也会连带删掉
; 对方的安装目录 —— 客户那边两版都装着时这是灾难。
AppId={#AppIdGuid}
; AppName 只能有一个，这里是双语的：英文用户看得出这是什么，中文用户认得出来。
; 安装目录名用纯 ASCII，避免中文路径给解压/杀软添麻烦。
AppName={#AppName} ({#AppNameEn})
AppVersion={#AppVer}
AppVerName={#AppName} {#AppVer}
AppPublisher={#AppPublisher}
; 不写这一行的话，安装包右键属性的文件版本会显示 "X"，看着像来路不明的程序
VersionInfoVersion={#AppVer4}
VersionInfoProductName={#AppName}
VersionInfoDescription={#AppName} 安装程序
VersionInfoCompany={#AppPublisher}
DefaultDirName={autopf}\{#InstallDirName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename={#OutputBase}_{#AppVer}
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
en.KeepDataPrompt=Also delete your glossary and translation cache?%n%nYour API key is kept either way - it is stored separately, so a reinstall will not ask for it again.%n%nChoose Yes to delete:
zh.KeepDataPrompt=是否一并删除术语库和翻译缓存？%n%nAPI key 无论选哪个都会保留：它单独存了一份，重装后不用重新填写。%n%n选「是」会删掉这些：
en.KeepDataTitle=Uninstall
zh.KeepDataTitle=卸载

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalTasks}"

[Files]
Source: "..\dist\{#AppExeName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 说明文档按发行版选。通用版放中英两份；定制版放自己那份（只讲差异），
; 另外把通用说明改名成「（通用版）」一起放进去当详细参考 ——
; 定制版说明没打算把一千行功能细节复制一遍，那份一改就要同步两处。
Source: "..\{#GuideFile}"; DestDir: "{app}"; DestName: "使用说明.md"; Flags: ignoreversion
#ifdef ExtraGuide
Source: "..\使用说明.md"; DestDir: "{app}"; DestName: "使用说明（通用版）.md"; Flags: ignoreversion
#else
Source: "..\USER-GUIDE.md"; DestDir: "{app}"; DestName: "User Guide (EN).md"; Flags: ignoreversion
#endif
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
// 覆盖安装之前，先把当前的 config.json 复制一份到数据目录的上一级。
//
// 为什么要这一步：卸载程序会问「术语库和翻译缓存要不要一起删」，用户点「是」，
// config.json 就没了 —— 翻译方向、输出单位、AI 模型名全部回到默认值。
// API key 另有 apikey.dat 单独兜着，但其余设置也值得救，毕竟用户调过一遍。
//
// 备份放在 data 的上一级：卸载删的是 data 子目录，备份在它外面就删不到。
// 程序启动时发现 config.json 不存在，会自动把它复制回来（见 translate.py
// 的 _restore_config_backup）。
procedure CurStepChanged(CurStep: TSetupStep);
var
  Src, Bak: String;
begin
  if CurStep = ssInstall then
  begin
    Src := ExpandConstant('{userappdata}\{#DataDirName}\data\config.json');
    Bak := ExpandConstant('{userappdata}\{#DataDirName}\config.bak.json');
    if FileExists(Src) then
    begin
      ForceDirectories(ExtractFileDir(Bak));
      // 第三个参数 False = 已存在就覆盖。备份要跟着最新配置走，
      // 留住第一次的那份只会在装了几轮之后还原出一个很旧的设置。
      // 用 CopyFile 不用 FileCopy：后者是 Inno 5 的名字，6.x 会出改名提示。
      CopyFile(Src, Bak, False);
    end;
  end;
end;

// 卸载收尾时问一句：术语库和翻译缓存要不要一起删。
// 直接删掉用户的术语库，是最容易挨骂的做法；直接留着，硬盘里又会悄悄堆几百兆。
//
// 注意这里**不再删整个** PDFDrawingTranslator 目录，只删里面的 data 子目录：
// 同级的 apikey.dat（加密保存的 API key）和 config.bak.json（安装前备份）
// 必须留着，否则用户装回来还得重新申请 key —— 而多数人填完 key 就没留底。
//
// 要问两个目录：一个是现在的 %APPDATA%\<本版数据目录>，
// 一个是老版本的 %APPDATA%\PDF图纸翻译。程序启动时会把老的搬进新的，
// 但搬不动（文件被占用、权限不足）时老目录还留在原地，卸载不能把它落下。
// 老目录没有 key 独立存储这套结构，整个删掉是对的。
//
// 老目录只有通用版才有。定制版不查它：同一台机器上可能装过通用版，
// 定制版卸载时去问「要不要删通用版的旧数据」属于越界 —— 那是别人的东西。
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, DataSub, LegacyDir, Targets: String;
  Found: Boolean;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{userappdata}\{#DataDirName}');
    DataSub := DataDir + '\data';
    LegacyDir := '';
#ifdef CheckLegacyDir
    LegacyDir := ExpandConstant('{userappdata}\PDF图纸翻译');
#endif
    Targets := '';
    Found := False;
    if DirExists(DataSub) then
    begin
      Targets := DataSub;
      Found := True;
    end;
    if (LegacyDir <> '') and DirExists(LegacyDir) then
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
        if DirExists(DataSub) then
          DelTree(DataSub, True, True, True);
        if (LegacyDir <> '') and DirExists(LegacyDir) then
          DelTree(LegacyDir, True, True, True);
      end;
    end;
  end;
end;

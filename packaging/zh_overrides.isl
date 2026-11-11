[LangOptions]
LanguageName=简体中文
LanguageID=$0804
LanguageCodePage=0
DialogFontName=Microsoft YaHei
DialogFontSize=9
WelcomeFontName=Microsoft YaHei
WelcomeFontSize=12
TitleFontName=Microsoft YaHei
TitleFontSize=29
CopyrightFontName=Microsoft YaHei
CopyrightFontSize=8

[Messages]
; 只覆盖向导里真正会看到的那几句，没写的自动落回英文 Default.isl
SetupAppTitle=安装程序
SetupWindowTitle=安装程序 - %1
UninstallAppTitle=卸载
UninstallAppFullTitle=卸载 %1
WelcomeLabel1=欢迎安装 [name]
WelcomeLabel2=即将把 [name] 装到你的电脑上。%n%n它可以把工程图纸 PDF 在中英之间对译，图形和版式不动，尺寸按你选的单位换算。%n%n关闭其它正在运行的程序，然后点「下一步」继续。
WizardSelectDir=选择安装位置
SelectDirDesc=装到哪里？
SelectDirLabel3=安装程序会把 [name] 装到下面的文件夹里。
SelectDirBrowseLabel=如果想换一个位置，点「浏览」。装完点「下一步」。
WizardSelectTasks=选择附加任务
SelectTasksDesc=要顺便做哪些事？
SelectTasksLabel2=选择安装 [name] 时要顺便完成的任务，然后点「下一步」。
WizardPreparing=准备安装
PreparingDesc=安装程序已就绪，即将把 [name] 装到你的电脑上。
WizardReady=准备安装
ReadyLabel1=现在可以开始安装了。
ReadyLabel2a=点「安装」开始，或者点「上一步」回去改设置。
ReadyLabel2b=点「安装」开始。
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候…
FinishedHeadingLabel=安装完成
FinishedLabelNoIcons=装好了。双击桌面上的「%1」就能用。%n%n第一次打开建议先拖一份 PDF 进去试，或者用安装目录下「示例图纸」里的那份。
FinishedLabel=装好了。双击桌面上的「%1」就能用。%n%n第一次打开建议先拖一份 PDF 进去试，或者用安装目录下「示例图纸」里的那份。
ClickNext=点「下一步」继续。
ButtonNext=下一步(&N) >
ButtonBack=< 上一步(&B)
ButtonInstall=安装(&I)
ButtonFinish=完成(&F)
ButtonCancel=取消
ButtonBrowse=浏览(&R)...
ButtonYes=是(&Y)
ButtonNo=否(&N)
ButtonOK=确定
ExitSetupTitle=退出安装
ExitSetupMessage=安装还没完成，现在退出程序不会装上。%n%n确定要退出吗？
ConfirmUninstall=确定要卸载 %1 吗？
UninstalledAll=%1 已经卸载完成。
WizardUninstalling=卸载进度
WizardUserInfo=用户信息
YesRadio=是，现在就重启电脑(&Y)
NoRadio=不，我自己稍后重启(&N)

[CustomMessages]
; 这一段对应官方 Default.isl 里的 [CustomMessages]。不补齐的话编译时
; 每条都会报一次「未定义、回落到英文」，而且用户真碰到时（比如卸载时
; 安装目录已被手动删掉）中文界面里会突然蹦一句英文，看着像程序出错。
NameAndVersion=%1 版本 %2
AdditionalIcons=附加快捷方式：
CreateDesktopIcon=创建桌面快捷方式(&D)
CreateQuickLaunchIcon=创建快速启动栏快捷方式(&Q)
ProgramOnTheWeb=%1 官网
UninstallProgram=卸载 %1
LaunchProgram=运行 %1
AssocFileExtension=把 %1 与 %2 文件扩展名关联(&A)
AssocingFileExtension=正在把 %1 与 %2 文件扩展名关联...
AutoStartProgramGroupDescription=启动：
AutoStartProgram=自动启动 %1
AddonHostProgramNotFound=在您选择的文件夹里没有找到 %1。%n%n要把它从应用列表里移除、继续卸载吗？

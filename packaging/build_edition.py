# -*- coding: utf-8 -*-
"""
按发行配置打包。

    python packaging/build_edition.py dynamic            # 打定制版（exe + 安装包）
    python packaging/build_edition.py standard           # 打通用版
    python packaging/build_edition.py dynamic --no-installer   # 只要 exe
    python packaging/build_edition.py --defs-only        # 只生成两份中间文件

它做的事情，按顺序：

    1. 读 editions/<id>.json，把根目录的 edition.json 换成目标版本的
       ——   程序运行时只认根目录那一份，所以这是「这一包是谁」的唯一开关
    2. 生成 packaging/_version_info_build.txt（exe 属性里的版本/产品名）
    3. 生成 packaging/edition_defs.iss（安装脚本里的品牌/版本/GUID）
    4. 调 PyInstaller
    5. 调 Inno Setup 编安装包
    6. 无论成功失败，把 edition.json 还原回原来的样子

第 6 步是重点。这个脚本会改动一个被版本控制跟踪的文件，中途被打断
（Ctrl+C、PyInstaller 报错、断电）都必须还原 —— 否则下一次打包会
悄悄带上上次的发行配置，打出来的包名字是错的，而且没有任何提示。
所以用 try/finally，不是判断成功后还原。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDITIONS_DIR = os.path.join(ROOT, "editions")
EDITION_JSON = os.path.join(ROOT, "edition.json")
DEFS_ISS = os.path.join(HERE, "edition_defs.iss")
VERSION_TXT = os.path.join(HERE, "version_info.txt")
VERSION_BUILD = os.path.join(HERE, "_version_info_build.txt")

# Inno Setup 的常见安装位置。找不到就让用户装，不猜别的路径。
ISCC_CANDIDATES = [
    r"C:\Users\stanl\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def die(msg):
    print("[失败] " + msg, file=sys.stderr)
    sys.exit(1)


def load_edition(eid):
    """读 editions/<id>.json。字段缺一个就打不出正确的包，所以在这里就拦住"""
    p = os.path.join(EDITIONS_DIR, eid + ".json")
    if not os.path.exists(p):
        avail = sorted(f[:-5] for f in os.listdir(EDITIONS_DIR)
                       if f.endswith(".json"))
        die(f"没有这个发行版：{eid}\n可用：{', '.join(avail)}")
    with open(p, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    need = ("id", "name_zh", "name_en", "version", "data_dir",
            "exe_name", "app_id_guid", "output_prefix")
    missing = [k for k in need if not str(cfg.get(k) or "").strip()]
    if missing:
        die(f"{p} 缺字段：{', '.join(missing)}")
    if cfg["id"] != eid:
        die(f"{p} 里的 id 是 {cfg['id']}，与文件名 {eid} 不一致")
    # 两版共用同一个 AppId 会导致后装的顶掉先装的，这个错误在客户机器上
    # 表现为「装完定制版，通用版不见了」，现场根本看不出是 GUID 撞了
    for other in os.listdir(EDITIONS_DIR):
        if not other.endswith(".json") or other[:-5] == eid:
            continue
        with open(os.path.join(EDITIONS_DIR, other), "r",
                  encoding="utf-8-sig") as f:
            o = json.load(f)
        if o.get("app_id_guid", "").lower() == cfg["app_id_guid"].lower():
            die(f"AppId 与 {other[:-5]} 重复：{cfg['app_id_guid']}")
        if o.get("data_dir", "").lower() == cfg["data_dir"].lower():
            die(f"数据目录与 {other[:-5]} 重复：{cfg['data_dir']}，"
                "两版会共用配置和缓存")
    return cfg


def write_defs(cfg):
    """生成安装脚本要的那几个 #define"""
    guid = cfg["app_id_guid"].strip()
    # Inno 的 AppId 值是 {GUID} 形式，而 ISPP 里 {{ 才是字面意义的 {，
    # 所以这里多补一个左括号。写错的表现是每次安装被当成全新安装，
    # 升级时旧的 Program Files 目录留在原地
    if not guid.startswith("{{"):
        guid = "{" + guid
    lines = [
        "; 本文件由 packaging/build_edition.py 生成，不要手改。",
        "; 改品牌/版本请改 editions/<id>.json，然后重新生成。",
        '#define AppName "%s"' % cfg["name_zh"],
        '#define AppNameEn "%s"' % cfg["name_en"],
        '#define AppVer "%s"' % cfg["version"],
        # 安装包的文件属性要求四段版本号，三位会显示成空白
        '#define AppVer4 "%s"' % ".".join(
            (cfg["version"].split(".") + ["0", "0", "0"])[:4]),
        '#define AppExeName "%s"' % cfg["exe_name"],
        '#define AppExe "%s.exe"' % cfg["exe_name"],
        '#define AppIdGuid "%s"' % guid,
        '#define InstallDirName "%s"' % cfg["data_dir"],
        '#define DataDirName "%s"' % cfg["data_dir"],
        '#define OutputBase "%s"' % cfg["output_prefix"],
        '#define AppPublisher "Walter"',
        # 安装包里放哪份说明。定制版放自己那份（只讲差异），
        # 通用说明改名成「（通用版）」一起放进去当详细参考 ——
        # 定制版说明改写自通用说明，但没打算把一千行的功能细节复制一遍，
        # 那份一改就要同步两份，迟早对不上。
        '#define GuideFile "%s"' % (cfg.get("guide") or "使用说明.md"),
    ]
    # 只有通用版需要清理历史上的中文数据目录；定制版没有这段历史，
    # 而它可能和通用版装在同一台机器上，不该去动别人的数据
    if cfg["id"] == "standard":
        lines.append("#define CheckLegacyDir")
    else:
        # 定制版额外带一份通用说明作参考，不放英文版（那是通用版的文档，
        # 里面写的名字和这版对不上）
        lines.append("#define ExtraGuide")
    with open(DEFS_ISS, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    print("  写 " + os.path.relpath(DEFS_ISS, ROOT))


def write_version_info(cfg):
    """按 version_info.txt 生成这一版的版本资源"""
    if not os.path.exists(VERSION_TXT):
        print("  跳过版本资源：没有 version_info.txt")
        return
    with open(VERSION_TXT, "r", encoding="utf-8-sig") as f:
        s = f.read()
    v = cfg["version"]
    seg = [str(int(x)) if x.isdigit() else "0" for x in v.split(".")]
    while len(seg) < 4:
        seg.append("0")
    v4 = ".".join(seg[:4])
    # 元组形式要写成 (1, 1, 10, 0)，写成 "1.1.10.0" 会让 PyInstaller
    # 在解析版本资源时抛语法错误，而报错指向调用处不是这里
    vt = "(%s)" % ", ".join(seg[:4])
    s = re.sub(r"filevers=\([^)]*\)", "filevers=%s" % vt, s)
    s = re.sub(r"prodvers=\([^)]*\)", "prodvers=%s" % vt, s)
    s = re.sub(r'"FileVersion", "[^"]*"', '"FileVersion", "%s"' % v4, s)
    s = re.sub(r'"ProductVersion", "[^"]*"', '"ProductVersion", "%s"' % v4, s)
    s = re.sub(r'"OriginalFilename", "[^"]*"',
               '"OriginalFilename", "%s.exe"' % cfg["exe_name"], s)
    s = re.sub(r'"ProductName", "[^"]*"',
               '"ProductName", "%s"' % cfg["name_zh"], s)
    s = re.sub(r'"InternalName", "[^"]*"',
               '"InternalName", "%s"' % cfg["exe_name"], s)
    # 定制版不是开源项目，属性里写「open source / MIT License」是错的，
    # 客户会拿着这个去问「我这个是不是盗版」
    if cfg["id"] != "standard":
        s = re.sub(r'"CompanyName", "[^"]*"',
                   '"CompanyName", "%s"' % cfg["name_zh"], s)
        s = re.sub(r'"LegalCopyright", "[^"]*"',
                   '"LegalCopyright", "%s"' % cfg["name_zh"], s)
        s = re.sub(r'"FileDescription",\s*"[^"]*"',
                   '"FileDescription", "%s"' % cfg["name_zh"], s)
    with open(VERSION_BUILD, "w", encoding="utf-8") as f:
        f.write(s)
    print("  写 " + os.path.relpath(VERSION_BUILD, ROOT))


def find_iscc():
    for p in ISCC_CANDIDATES:
        if os.path.exists(p):
            return p
    w = shutil.which("iscc")
    if w:
        return w
    return None


def run(cmd, cwd=ROOT):
    print("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=cwd)
    if r.returncode != 0:
        die("命令失败（退出码 %d）：%s" % (r.returncode, " ".join(cmd)))


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("edition", nargs="?", default=None,
                    help="发行版 id（editions/ 下的文件名），缺省用当前的")
    ap.add_argument("--defs-only", action="store_true",
                    help="只生成 edition_defs.iss 与版本资源，不打包")
    ap.add_argument("--no-installer", action="store_true",
                    help="只打 exe，不编安装包")
    args = ap.parse_args()

    if args.edition:
        eid = args.edition
    else:
        with open(EDITION_JSON, "r", encoding="utf-8-sig") as f:
            eid = (json.load(f).get("id") or "standard")
    cfg = load_edition(eid)
    print("发行版：%s  %s  v%s" % (cfg["id"], cfg["name_zh"], cfg["version"]))

    if args.defs_only:
        write_defs(cfg)
        write_version_info(cfg)
        return

    # 备份当前 edition.json。它被 git 跟踪，改完必须还原
    backup = None
    if os.path.exists(EDITION_JSON):
        with open(EDITION_JSON, "rb") as f:
            backup = f.read()
    try:
        with open(EDITION_JSON, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print("  写 edition.json → " + cfg["id"])

        write_defs(cfg)
        write_version_info(cfg)

        run([sys.executable, "-m", "PyInstaller",
             os.path.join("packaging", "PDFTranslator.spec"), "--noconfirm"])

        if not args.no_installer:
            iscc = find_iscc()
            if not iscc:
                die("找不到 Inno Setup 的 ISCC.exe，装上再跑，"
                    "或者加 --no-installer 只打 exe")
            run([iscc, os.path.join("packaging", "installer.iss")])
    finally:
        if backup is not None:
            with open(EDITION_JSON, "wb") as f:
                f.write(backup)
            print("  edition.json 已还原")
        # 版本资源是临时文件：留在仓库里会让下一次打包误用上一版的号
        if os.path.exists(VERSION_BUILD):
            try:
                os.remove(VERSION_BUILD)
            except Exception:
                pass

    out = os.path.join(HERE, "output")
    if os.path.isdir(out):
        hits = [f for f in os.listdir(out)
                if f.startswith(cfg["output_prefix"] + "_" + cfg["version"])]
        for h in hits:
            p = os.path.join(out, h)
            print("产物：%s  %.1f MB" % (h, os.path.getsize(p) / 1048576.0))


if __name__ == "__main__":
    main()

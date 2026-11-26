// dshapps —— 极简宿主：把 ~/apps/* 里的 app 列出来，点开就能跑。
//
// 设计约束（来自 docs/10 与 docs/11）：
//   · 窗口用系统 WebView2，不打包 Chromium（实测 3 MB / 460 ms）
//   · 一个进程管所有 app，边际成本递减（不搞「每个工具一个 exe」）
//   · 事件流是唯一真相：spawn dsh --json，逐行 NDJSON 上屏
//   · 权限收在这里：cwd = app 目录，DSH_PERMISSION_MODE 由 app.json 决定
//   · 失败要说人话：spawn 失败/退出码非 0 都要出现在 UI 里，不许静默
//
// 用法：
//   go build -ldflags "-s -w -H windowsgui" -o dshapps.exe .
//   dshapps.exe                      # 默认扫 ~/apps
//   dshapps.exe -root D:\apps        # 指定根目录
//
// 环境变量：
//   DSH_KIT_CRED_PATCH   可选，指向 configs/cred.patch.yml（headless 需要）
//   DSH_KIT_BROKER_URL   可选，走凭证 broker 时设置
package main

import (
	"bufio"
	_ "embed"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	webview2 "github.com/jchv/go-webview2"
)

//go:embed ui/index.html
var indexHTML string

type AppMeta struct {
	Version int    `json:"version"`
	Name    string `json:"name"`
	Slug    string `json:"slug"`
	Desc    string `json:"desc"`
	Profile string `json:"profile"`
	Surface string `json:"surface"`
	Sandbox string `json:"sandbox"`
	Network bool   `json:"network"`
	Created string `json:"created"`
}

type AppView struct {
	Name     string `json:"name"`
	Slug     string `json:"slug"`
	Desc     string `json:"desc"`
	Profile  string `json:"profile"`
	Sandbox  string `json:"sandbox"`
	Network  bool   `json:"network"`
	Dir      string `json:"dir"`
	HasCases bool   `json:"hasCases"`
	Bad      string `json:"bad,omitempty"` // 读取失败的原因，UI 要显出来
}

type host struct {
	w        webview2.WebView
	root     string
	mu       sync.Mutex
	proc     *exec.Cmd
	running  string
	seq      int64
	credPath string
	broker   *credentialBroker
	patch    string // 宿主自动生成的 patch（凭证占位符 + broker 地址）
}

// ---------------------------------------------------------------- 扫描
func (h *host) scan() []AppView {
	entries, err := os.ReadDir(h.root)
	if err != nil {
		return []AppView{}
	}
	out := make([]AppView, 0, len(entries))
	for _, e := range entries {
		if !e.IsDir() || strings.HasPrefix(e.Name(), "_") || strings.HasPrefix(e.Name(), ".") {
			continue
		}
		dir := filepath.Join(h.root, e.Name())
		v := AppView{Name: e.Name(), Dir: dir, Sandbox: "workspace-write"}
		raw, err := os.ReadFile(filepath.Join(dir, "app.json"))
		if err != nil {
			v.Bad = "缺 app.json（不是本宿主生成的 app）"
			out = append(out, v)
			continue
		}
		var meta AppMeta
		if err := json.Unmarshal(raw, &meta); err != nil {
			v.Bad = "app.json 解析失败: " + err.Error()
			out = append(out, v)
			continue
		}
		if meta.Name != "" {
			v.Name = meta.Name
		}
		v.Slug, v.Desc, v.Profile = meta.Slug, meta.Desc, meta.Profile
		if meta.Sandbox != "" {
			v.Sandbox = meta.Sandbox
		}
		v.Network = meta.Network
		if v.Profile == "" {
			v.Profile = "app-" + v.Slug
		}
		if _, err := os.Stat(filepath.Join(dir, "cases.json")); err == nil {
			v.HasCases = true
		}
		out = append(out, v)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// ---------------------------------------------------------------- 事件推送
func (h *host) emit(ev map[string]any) {
	b, err := json.Marshal(ev)
	if err != nil {
		return
	}
	payload := strconv.Quote(string(b))
	h.w.Dispatch(func() {
		h.w.Eval("window.__onEvent && window.__onEvent(" + payload + ")")
	})
}

// emitNow 用于**已经在主线程**（webview RPC 回调里）的场景，直接 Eval。
// 走 Dispatch 会把任务排到队列，等 RPC 返回后主循环才执行 —— 实测顺序不稳。
func (h *host) emitNow(ev map[string]any) {
	b, err := json.Marshal(ev)
	if err != nil {
		return
	}
	payload := strconv.Quote(string(b))
	h.w.Eval("window.__onEvent && window.__onEvent(" + payload + ")")
}

// ---------------------------------------------------------------- 命令拼装
func dshLauncher() []string {
	exe, err := exec.LookPath("dsh")
	if err != nil {
		return nil
	}
	lower := strings.ToLower(exe)
	if runtime.GOOS == "windows" && (strings.HasSuffix(lower, ".cmd") || strings.HasSuffix(lower, ".bat")) {
		// Windows 上直接 Popen 一个 .cmd 会失败，必须过 cmd /c
		return []string{"cmd", "/c", exe}
	}
	return []string{exe}
}

func (h *host) buildCmd(app AppView, task string) (*exec.Cmd, error) {
	launcher := dshLauncher()
	if len(launcher) == 0 {
		return nil, fmt.Errorf("PATH 里找不到 dsh（先装 @deepseek-ai/dsh）")
	}
	argv := append([]string{}, launcher...)
	if h.patch != "" {
		// --patch 是启动器 flag，必须写在 app 参数之前
		argv = append(argv, "--patch", h.patch)
	} else if h.credPath != "" {
		argv = append(argv, "--patch", h.credPath)
	}
	argv = append(argv, "--profile", app.Profile, "--json", "-")

	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = app.Dir
	cmd.Env = append(os.Environ(), "DSH_PERMISSION_MODE="+app.Sandbox)
	cmd.Env = append(cmd.Env, "PYTHONIOENCODING=utf-8")
	// GUI 子系统没有控制台，子进程里的 pwsh 工具会挂住 —— 见 proc_windows.go
	prepareCmd(cmd)
	return cmd, nil
}

// ---------------------------------------------------------------- 运行
func (h *host) run(appDir, task string) map[string]any {
	dbg("hostRun 进入: app=%s task=%q", appDir, task)
	h.mu.Lock()
	if h.proc != nil {
		h.mu.Unlock()
		return map[string]any{"ok": false, "err": "已有任务在跑，先停止或等它结束"}
	}
	h.mu.Unlock()

	var app AppView
	for _, a := range h.scan() {
		if filepath.Clean(a.Dir) == filepath.Clean(appDir) {
			app = a
			break
		}
	}
	if app.Dir == "" {
		return map[string]any{"ok": false, "err": "找不到这个 app（可能已被删除）"}
	}
	if app.Bad != "" {
		return map[string]any{"ok": false, "err": app.Bad}
	}

	cmd, err := h.buildCmd(app, task)
	if err != nil {
		return map[string]any{"ok": false, "err": err.Error()}
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return map[string]any{"ok": false, "err": "拿不到 stdout: " + err.Error()}
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return map[string]any{"ok": false, "err": "拿不到 stderr: " + err.Error()}
	}
	cmd.Stdin = strings.NewReader(task)
	if err := cmd.Start(); err != nil {
		dbg("cmd.Start 失败: %v", err)
		return map[string]any{"ok": false, "err": "启动失败: " + err.Error()}
	}
	dbg("cmd.Start 成功 pid=%d", cmd.Process.Pid)

	h.mu.Lock()
	h.proc = cmd
	h.running = app.Name
	h.seq++
	runID := h.seq
	h.mu.Unlock()

	h.emitNow(map[string]any{"type": "_start", "app": app.Name, "run": runID,
		"cmd": strings.Join(cmd.Args, " ")})

	// stderr 单独收集：dsh 的诊断都在这里，必须显给用户，不能丢
	go func() {
		sc := bufio.NewScanner(stderr)
		sc.Buffer(make([]byte, 0, 64<<10), 4<<20)
		for sc.Scan() {
			line := sc.Text()
			if strings.TrimSpace(line) == "" {
				continue
			}
			h.emit(map[string]any{"type": "_stderr", "text": line})
		}
	}()

	// stdout 只负责读；**读完就收尾**。
	//
	// 实测（2026-11-26 黑盒对照）：dsh 输出完 `final` 后 stdout 先 EOF，
	// 但 node 进程还要再活一会儿。如果等进程退出才收尾，UI 就会一直转圈。
	// 所以顺序是：EOF → （最多等 1.5 s 拿真实退出码）→ 发 _end → 异步收尸。
	go func() {
		sc := bufio.NewScanner(stdout)
		sc.Buffer(make([]byte, 0, 64<<10), 32<<20) // 大输出不截断
		for sc.Scan() {
			raw := strings.TrimSpace(sc.Text())
			if raw == "" {
				continue
			}
			var ev map[string]any
			if err := json.Unmarshal([]byte(raw), &ev); err != nil {
				// 协议降级：非 JSON 行原样保留（docs/02 的 _raw 约定）
				h.emit(map[string]any{"type": "_raw", "text": raw})
				continue
			}
			h.emit(ev)
		}
		if err := sc.Err(); err != nil {
			dbg("stdout 扫描出错: %v", err)
		}
		dbg("stdout EOF")

		exitCh := make(chan int, 1)
		go func() {
			state, werr := cmd.Process.Wait()
			if werr != nil || state == nil {
				exitCh <- -1
				return
			}
			exitCh <- state.ExitCode()
		}()

		code := -1
		select {
		case code = <-exitCh:
		case <-time.After(1500 * time.Millisecond):
			code = -1 // 进程还没退：先让 UI 收尾，收尸放到后面异步做
		}

		_ = stdout.Close()
		_ = stderr.Close()
		h.mu.Lock()
		h.proc = nil
		h.running = ""
		h.mu.Unlock()
		h.emit(map[string]any{"type": "_end", "code": code})
		dbg("已发 _end code=%d", code)

		if runtime.GOOS == "windows" {
			go func(pid int) {
				c := exec.Command("taskkill", "/T", "/F", "/PID", strconv.Itoa(pid))
				prepareCmd(c)
				_ = c.Run()
				dbg("taskkill 完成 pid=%d", pid)
			}(cmd.Process.Pid)
		}
	}()

	dbg("hostRun 返回 ok run=%d", runID)
	return map[string]any{"ok": true, "run": runID}
}

func (h *host) stop() map[string]any {
	h.mu.Lock()
	cmd := h.proc
	h.mu.Unlock()
	if cmd == nil || cmd.Process == nil {
		return map[string]any{"ok": false, "err": "没有正在跑的任务"}
	}
	_ = cmd.Process.Kill()
	return map[string]any{"ok": true}
}

// ---------------------------------------------------------------- main
func main() {
	root := flag.String("root", "", "apps 根目录（默认 ~/apps）")
	cred := flag.String("cred", os.Getenv("DSH_KIT_CRED_PATCH"), "credentials patch 路径（可选）")
	flag.Parse()

	home, _ := os.UserHomeDir()
	if *root == "" {
		*root = filepath.Join(home, "apps")
	}
	rootAbs, err := filepath.Abs(*root)
	if err != nil {
		rootAbs = *root
	}
	if _, err := os.Stat(rootAbs); err != nil {
		_ = os.MkdirAll(rootAbs, 0o755)
	}

	h := &host{root: rootAbs, credPath: *cred}

	// 凭证 broker：宿主持有真 key，子进程只拿占位符（docs/10 的 L1）
	if b, err := startBroker("https://api.deepseek.com/anthropic"); err == nil {
		h.broker = b
		if b.hasKey {
			tmp := filepath.Join(os.TempDir(), "dshapps")
			if p, perr := writeSandboxPatch(tmp, b.url()); perr == nil {
				h.patch = p
				dbg("已生成 sandbox patch: %s", p)
			} else {
				dbg("生成 patch 失败: %v", perr)
			}
		}
	} else {
		dbg("broker 启动失败: %v", err)
	}

	w := webview2.NewWithOptions(webview2.WebViewOptions{
		Debug:     false,
		AutoFocus: true,
		WindowOptions: webview2.WindowOptions{
			Title:  "dsh apps",
			Width:  1080,
			Height: 720,
			Center: true,
		},
	})
	if w == nil {
		fmt.Fprintln(os.Stderr, "WebView2 初始化失败：请确认系统已安装 WebView2 Runtime")
		os.Exit(1)
	}
	h.w = w
	defer w.Destroy()
	defer func() { h.broker.close() }()

	_ = w.Bind("hostApps", func() string {
		b, _ := json.Marshal(map[string]any{"root": h.root, "apps": h.scan()})
		return string(b)
	})
	_ = w.Bind("hostRun", func(appDir, task string) string {
		b, _ := json.Marshal(h.run(appDir, task))
		return string(b)
	})
	_ = w.Bind("hostStop", func() string {
		b, _ := json.Marshal(h.stop())
		return string(b)
	})
	_ = w.Bind("hostOpen", func(appDir string) string {
		// 只允许打开 apps 根目录下的路径
		clean := filepath.Clean(appDir)
		if !strings.HasPrefix(clean, h.root) {
			return `{"ok":false,"err":"路径越界"}`
		}
		_ = exec.Command("explorer", clean).Start()
		return `{"ok":true}`
	})
	_ = w.Bind("hostCredState", func() string {
		b, _ := json.Marshal(inspectCredentials())
		return string(b)
	})
	_ = w.Bind("hostMigrateKey", func() string {
		backup, err := migrateKeyToStore()
		if err != nil {
			b, _ := json.Marshal(map[string]any{"ok": false, "err": err.Error()})
			return string(b)
		}
		b, _ := json.Marshal(map[string]any{"ok": true, "backup": backup})
		return string(b)
	})
	_ = w.Bind("hostRestoreKey", func() string {
		path, err := restoreLatestBackup()
		if err != nil {
			b, _ := json.Marshal(map[string]any{"ok": false, "err": err.Error()})
			return string(b)
		}
		b, _ := json.Marshal(map[string]any{"ok": true, "backup": path})
		return string(b)
	})
	_ = w.Bind("hostVersion", func() string {
		b, _ := json.Marshal(map[string]any{
			"go": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH,
			"root": h.root, "cred": h.credPath,
			"broker": h.broker != nil && h.broker.hasKey, "patch": h.patch,
		})
		return string(b)
	})

	w.SetHtml(indexHTML)
	w.Run()

	// 窗口关掉时顺手清掉子进程，避免孤儿
	h.mu.Lock()
	if h.proc != nil && h.proc.Process != nil {
		_ = h.proc.Process.Kill()
	}
	h.mu.Unlock()
}

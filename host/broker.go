// 内嵌凭证 broker：宿主自己就是那个「持有真 key 的人」。
//
// 为什么不做成外部依赖（Python 的 dsh_agent_kit.broker）：
//   宿主是单文件 exe，拉一个 Python 进程进来就破坏了这个前提。
//   Go 标准库的 httputil.ReverseProxy 正好够用，且天然支持流式。
//
// 闭环怎么合上（关键是 dsh-credentials-local 支持 `path` 配置）：
//   1. 宿主读真 key（环境变量或 ~/.dsh/.credentials.yaml），只放在内存
//   2. 起本地反代，转发时注入真 key
//   3. 写一份**只有占位符**的 credentials 文件，用 patch 把 dsh 的
//      credentials.path 指过去
//   4. 于是 agent 读遍磁盘，拿到的是占位符；真 key 只在这个进程的堆里
//
// 边界（与 docs/10 一致）：
//   挡得住「读文件」，挡不住「读本进程内存」。后者要 L2。
package main

import (
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

const placeholderKey = "broker-placeholder-not-a-secret"

type credentialBroker struct {
	server *http.Server
	port   int
	hasKey bool
}

// readRealKey 从环境变量或 harness home 的凭据文件里取真 key。
func readRealKey() string {
	// 顺序：宿主密钥库（DPAPI 密文）> 环境变量 > 明文文件
	if k := loadStoredKey(); k != "" {
		dbg("key 来源: 密钥库")
		return k
	}
	if k := strings.TrimSpace(os.Getenv("DEEPSEEK_API_KEY")); k != "" {
		dbg("key 来源: 环境变量")
		return k
	}
	dbg("key 来源: 明文文件")
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	path := os.Getenv("DSH_HOME")
	if path == "" {
		path = filepath.Join(home, ".dsh")
	}
	raw, err := os.ReadFile(filepath.Join(path, ".credentials.yaml"))
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(raw), "\n") {
		s := strings.TrimSpace(line)
		if !strings.HasPrefix(s, "DEEPSEEK_API_KEY:") {
			continue
		}
		v := strings.TrimSpace(strings.TrimPrefix(s, "DEEPSEEK_API_KEY:"))
		v = strings.Trim(v, `"'`)
		if v != "" && !strings.HasPrefix(v, "broker-placeholder") {
			return v
		}
	}
	return ""
}

// startBroker 起一个本地反代，把占位符换成真 key 后转发到上游。
// key 为空时返回 hasKey=false 的实例（不监听，调用方据此决定是否启用）。
func startBroker(upstream string) (*credentialBroker, error) {
	b := &credentialBroker{}
	key := readRealKey()
	if key == "" {
		dbg("broker: 找不到真 key，跳过（dsh 会自己找凭证）")
		return b, nil
	}

	target, err := url.Parse(upstream)
	if err != nil || target.Host == "" {
		return nil, fmt.Errorf("upstream 非法: %q", upstream)
	}

	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.FlushInterval = -1 // 立刻 flush：SSE 必须逐块透传
	inner := proxy.Director
	proxy.Director = func(req *http.Request) {
		// NewSingleHostReverseProxy 的默认 Director **已经**按 singleJoiningSlash
		// 拼好 scheme/host/path —— 再手动拼一次会得到
		// `/anthropic/anthropic/v1/messages`（实测 404 才发现）。
		inner(req)
		req.Host = target.Host
		req.Header.Del("Authorization")
		req.Header.Set("x-api-key", key) // ← 唯一注入点
		dbg("broker: %s %s", req.Method, req.URL.Path)
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		dbg("broker 上游失败: %v", err)
		w.Header().Set("content-type", "application/json")
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"type": "error",
			"error": map[string]string{
				"type": "broker_upstream_error", "message": err.Error(),
			},
		})
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}
	b.port = ln.Addr().(*net.TCPAddr).Port
	b.hasKey = true
	b.server = &http.Server{Handler: proxy}
	go func() { _ = b.server.Serve(ln) }()
	dbg("broker 就绪 127.0.0.1:%d → %s", b.port, upstream)
	return b, nil
}

func (b *credentialBroker) url() string {
	return fmt.Sprintf("http://127.0.0.1:%d", b.port)
}

func (b *credentialBroker) close() {
	if b != nil && b.server != nil {
		_ = b.server.Close()
	}
}

// writeSandboxPatch 生成给 dsh 的补丁：把凭证指向占位符文件，把 LLM 指向 broker。
//
// 为什么改 credentials.path 就够（实测依据）：dsh-credentials-local 的查找优先级是
// 「存储文件 > 启动环境 > .env」，所以只要存储文件里是占位符，agent 就算自己
// export 一个环境变量也盖不过它。
func writeSandboxPatch(dir string, brokerURL string) (patchPath string, err error) {
	fakeCreds := filepath.Join(dir, "dshapps-creds.yml")
	creds := "# 由 dshapps 生成的占位符凭据：真 key 不在磁盘上\n" +
		"version: 1\nrefs:\n  DEEPSEEK_API_KEY: " + placeholderKey + "\n"
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	if err := os.WriteFile(fakeCreds, []byte(creds), 0o600); err != nil {
		return "", err
	}

	patch := strings.Join([]string{
		"# 由 dshapps 自动生成",
		"- id: credentials",
		"  config:",
		"    path: " + strings.ReplaceAll(fakeCreds, "\\", "/"),
		"- id: llm-deepseek",
		"  config:",
		"    baseURL: " + brokerURL,
		"",
	}, "\n")
	patchPath = filepath.Join(dir, "dshapps-sandbox.patch.yml")
	if err := os.WriteFile(patchPath, []byte(patch), 0o600); err != nil {
		return "", err
	}
	return patchPath, nil
}

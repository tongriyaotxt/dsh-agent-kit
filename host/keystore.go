// 宿主密钥库：把真 key 从「任何进程都能 read 的文本文件」搬进 DPAPI 密文。
//
// 这是 D1 的另一半（docs/10 第六节）：broker 只切断网络出口，文件出口还开着。
// 这里补的就是文件出口 —— 代价要说清：
//   迁移之后，**其他直接跑 dsh 的会话也拿不到 key 了**，必须同样挂 broker。
//   所以必须由用户点确认，绝不自动做。
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const refsPlaceholder = "broker-placeholder-not-a-secret"

func keystorePath() string {
	base := os.Getenv("LOCALAPPDATA")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".local", "share")
	}
	return filepath.Join(base, "dshapps", "secret.bin")
}

func credentialsPath() string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	base := os.Getenv("DSH_HOME")
	if base == "" {
		base = filepath.Join(home, ".dsh")
	}
	return filepath.Join(base, ".credentials.yaml")
}

// loadStoredKey 从密钥库读真 key（DPAPI 解密）。没有则返回空串。
func loadStoredKey() string {
	cipher, err := os.ReadFile(keystorePath())
	if err != nil {
		return ""
	}
	plain, err := dpapiUnprotect(cipher)
	if err != nil {
		dbg("密钥库解密失败: %v", err)
		return ""
	}
	return strings.TrimSpace(string(plain))
}

func saveStoredKey(key string) error {
	cipher, err := dpapiProtect([]byte(key))
	if err != nil {
		return err
	}
	path := keystorePath()
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	return os.WriteFile(path, cipher, 0o600)
}

// credentialState 描述磁盘上的凭据文件现在是什么状态，供 UI 显示。
type credentialState struct {
	Path     string `json:"path"`
	Exists   bool   `json:"exists"`
	HasReal  bool   `json:"hasReal"`  // refs 里有非占位符的值
	InStore  bool   `json:"inStore"`  // 密钥库里已有 key
	Backups  int    `json:"backups"`
}

func inspectCredentials() credentialState {
	path := credentialsPath()
	st := credentialState{Path: path}
	if path == "" {
		return st
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return st
	}
	st.Exists = true
	if v := refsValue(string(raw)); v != "" && !strings.HasPrefix(v, "broker-placeholder") {
		st.HasReal = true
	}
	if loadStoredKey() != "" {
		st.InStore = true
	}
	if backups, err := filepath.Glob(path + ".bak-dshapps-*"); err == nil {
		st.Backups = len(backups)
	}
	return st
}

// refsValue 取出 refs.DEEPSEEK_API_KEY 的值（行级解析，不引入 YAML 依赖）。
func refsValue(text string) string {
	inRefs := false
	for _, line := range strings.Split(text, "\n") {
		trimmed := strings.TrimLeft(line, " \t")
		indented := len(trimmed) != len(line)
		if !indented {
			inRefs = strings.TrimSpace(line) == "refs:"
			continue
		}
		if !inRefs {
			continue
		}
		if strings.HasPrefix(trimmed, "DEEPSEEK_API_KEY:") {
			v := strings.TrimSpace(strings.TrimPrefix(trimmed, "DEEPSEEK_API_KEY:"))
			return strings.Trim(v, `"'`)
		}
	}
	return ""
}

// redactRefs 只替换 refs 里那一行，**逐行改**，不重排 YAML。
//
// 为什么不用 YAML 库：这个文件里有 `records`（grant 的 payload 被逐字保留，
// 只有它的拥有者能解释）。序列化一遍等于把别人的数据重写，风险远大于收益。
func redactRefs(text, placeholder string) (string, bool) {
	lines := strings.Split(text, "\n")
	inRefs := false
	changed := false
	for i, line := range lines {
		trimmed := strings.TrimLeft(line, " \t")
		indented := len(trimmed) != len(line)
		if !indented {
			inRefs = strings.TrimSpace(line) == "refs:"
			continue
		}
		if !inRefs || !strings.HasPrefix(trimmed, "DEEPSEEK_API_KEY:") {
			continue
		}
		indent := line[:len(line)-len(trimmed)]
		lines[i] = indent + "DEEPSEEK_API_KEY: " + placeholder
		changed = true
	}
	return strings.Join(lines, "\n"), changed
}

// stillRedacted 判断文件内容是否已经是安全的（refs 里没有真 key）。
func stillRedacted(text string) bool {
	v := refsValue(text)
	return v == "" || strings.HasPrefix(v, "broker-placeholder")
}

// migrateKeyToStore 把文件里的真 key 搬进密钥库，并把它从文件里抹掉。
// 返回备份路径，供用户反悔时恢复。
func migrateKeyToStore() (backupPath string, err error) {
	path := credentialsPath()
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("读不到 %s: %w", path, err)
	}
	text := string(raw)
	key := refsValue(text)
	if key == "" {
		return "", fmt.Errorf("文件里没有 DEEPSEEK_API_KEY")
	}
	if strings.HasPrefix(key, "broker-placeholder") {
		return "", fmt.Errorf("文件里已经是占位符，没什么可迁移的")
	}
	redacted, changed := redactRefs(text, refsPlaceholder)
	if !changed {
		return "", fmt.Errorf("没能定位到 refs 里的那一行，已放弃（文件未改动）")
	}

	// 先落库，再改文件：顺序反了会出现「文件没了、库里也没有」的窗口
	if err := saveStoredKey(key); err != nil {
		return "", fmt.Errorf("写入密钥库失败（文件未改动）: %w", err)
	}
	backupPath = fmt.Sprintf("%s.bak-dshapps-%s", path, time.Now().Format("20060102-150405"))
	if err := os.WriteFile(backupPath, raw, 0o600); err != nil {
		return "", fmt.Errorf("备份失败（文件未改动）: %w", err)
	}
	if err := os.WriteFile(path, []byte(redacted), 0o600); err != nil {
		return "", fmt.Errorf("改写失败，备份在 %s: %w", backupPath, err)
	}

	// **写完立刻复读自检**。
	//
	// 实测（2026-11-26）：另一个还在跑的 dsh 会话持有 key 的内存快照，
	// 它会在稍后把整个 credentials.yaml 重写回去，把占位符覆盖掉。
	// 光「写完就算成功」是自欺，所以这里必须验证，被覆盖就自动回滚。
	time.Sleep(600 * time.Millisecond)
	again, rerr := os.ReadFile(path)
	if rerr != nil {
		return "", fmt.Errorf("复读失败: %w", rerr)
	}
	if !stillRedacted(string(again)) {
		_ = os.WriteFile(path, raw, 0o600)
		_ = os.Remove(backupPath)
		_ = os.Remove(keystorePath())
		return "", fmt.Errorf(
			"迁移被其他 dsh 进程覆盖了（它们持有 key 的内存快照）。" +
				"已回滚，文件保持原样。请先关掉其他 dsh 会话再试")
	}

	dbg("已迁移 key 到密钥库，备份 %s", backupPath)
	return backupPath, nil
}

// restoreLatestBackup 恢复最近一次迁移。给「后悔」用。
func restoreLatestBackup() (string, error) {
	path := credentialsPath()
	backups, err := filepath.Glob(path + ".bak-dshapps-*")
	if err != nil || len(backups) == 0 {
		return "", fmt.Errorf("没有找到备份")
	}
	sort.Strings(backups)
	latest := backups[len(backups)-1]
	raw, err := os.ReadFile(latest)
	if err != nil {
		return "", err
	}
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		return "", err
	}
	// 同步抹掉密钥库，避免「文件里也有、库里也有」两个来源
	_ = os.Remove(keystorePath())
	return latest, nil
}

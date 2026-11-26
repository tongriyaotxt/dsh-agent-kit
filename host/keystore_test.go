package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// 真实的 credentials.yaml 形状（2026-11-26 抓的键名，值已替换）
const sampleCreds = `version: 1
refs:
  DEEPSEEK_API_KEY: sk-realkey-should-be-moved
records:
  client-connection/browser-session:
    kind: grant
    payload:
      version: 1
      secret: cf5abcdef
`

func setupTemp(t *testing.T) (credsPath string) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("DSH_HOME", dir)
	t.Setenv("LOCALAPPDATA", dir)
	credsPath = filepath.Join(dir, ".credentials.yaml")
	if err := os.WriteFile(credsPath, []byte(sampleCreds), 0o600); err != nil {
		t.Fatal(err)
	}
	return credsPath
}

func TestRefsValueParsesOnlyRefs(t *testing.T) {
	if got := refsValue(sampleCreds); got != "sk-realkey-should-be-moved" {
		t.Fatalf("refsValue = %q", got)
	}
	// records 里的 secret 不能被误当成 key
	if got := refsValue("records:\n  x:\n    secret: abc\n"); got != "" {
		t.Fatalf("records 段被误读成 refs: %q", got)
	}
}

func TestRedactRefsTouchesOnlyThatLine(t *testing.T) {
	out, changed := redactRefs(sampleCreds, refsPlaceholder)
	if !changed {
		t.Fatal("没有识别到要替换的行")
	}
	if strings.Contains(out, "sk-realkey-should-be-moved") {
		t.Fatal("真 key 还在输出里")
	}
	if !strings.Contains(out, "DEEPSEEK_API_KEY: "+refsPlaceholder) {
		t.Fatal("占位符没写进去")
	}
	// records 段必须一字不动（grant 的 payload 只有拥有者能解释）
	wantTail := "records:\n  client-connection/browser-session:\n    kind: grant\n    payload:\n      version: 1\n      secret: cf5abcdef\n"
	if !strings.HasSuffix(out, wantTail) {
		t.Fatalf("records 段被改动了:\n%s", out)
	}
	if strings.Count(out, "\n") != strings.Count(sampleCreds, "\n") {
		t.Fatal("行数变了，说明不是行级替换")
	}
}

func TestInspectReportsRealKey(t *testing.T) {
	setupTemp(t)
	st := inspectCredentials()
	if !st.Exists || !st.HasReal {
		t.Fatalf("应检测到明文 key: %+v", st)
	}
	if st.InStore {
		t.Fatal("还没迁移，不该有密钥库")
	}
}

func TestMigrateThenRestore(t *testing.T) {
	creds := setupTemp(t)

	backup, err := migrateKeyToStore()
	if err != nil {
		t.Fatalf("迁移失败: %v", err)
	}
	if _, err := os.Stat(backup); err != nil {
		t.Fatalf("备份文件不存在: %v", err)
	}

	after, _ := os.ReadFile(creds)
	if strings.Contains(string(after), "sk-realkey-should-be-moved") {
		t.Fatal("迁移后文件里还有真 key")
	}
	if !strings.Contains(string(after), refsPlaceholder) {
		t.Fatal("迁移后没有占位符")
	}
	if !strings.Contains(string(after), "secret: cf5abcdef") {
		t.Fatal("迁移把 records 弄坏了")
	}

	st := inspectCredentials()
	if st.HasReal {
		t.Fatal("迁移后不应再检测到明文 key")
	}
	if !st.InStore {
		t.Fatal("迁移后密钥库里应该有 key")
	}

	// broker 侧应该能从密钥库读到真 key
	if got := readRealKey(); got != "sk-realkey-should-be-moved" {
		t.Fatalf("readRealKey = %q（应来自密钥库）", got)
	}

	// 恢复
	restored, err := restoreLatestBackup()
	if err != nil {
		t.Fatalf("恢复失败: %v", err)
	}
	if restored != backup {
		t.Fatalf("恢复的不是最近那份: %s vs %s", restored, backup)
	}
	back, _ := os.ReadFile(creds)
	if !strings.Contains(string(back), "sk-realkey-should-be-moved") {
		t.Fatal("恢复后真 key 应该回来")
	}
	if got := loadStoredKey(); got != "" {
		t.Fatal("恢复后密钥库应被清空，避免两个来源")
	}
}

func TestMigrateRefusesAlreadyRedacted(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DSH_HOME", dir)
	t.Setenv("LOCALAPPDATA", dir)
	path := filepath.Join(dir, ".credentials.yaml")
	_ = os.WriteFile(path, []byte("version: 1\nrefs:\n  DEEPSEEK_API_KEY: "+refsPlaceholder+"\n"), 0o600)

	if _, err := migrateKeyToStore(); err == nil {
		t.Fatal("已经是占位符时不该重复迁移")
	}
}

func TestMigrateLeavesFileUntouchedWhenNoKey(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("DSH_HOME", dir)
	t.Setenv("LOCALAPPDATA", dir)
	path := filepath.Join(dir, ".credentials.yaml")
	original := "version: 1\nrefs: {}\n"
	_ = os.WriteFile(path, []byte(original), 0o600)

	if _, err := migrateKeyToStore(); err == nil {
		t.Fatal("没有 key 时应报错")
	}
	after, _ := os.ReadFile(path)
	if string(after) != original {
		t.Fatal("失败时文件必须保持原样")
	}
}

// 复现 2026-11-26 的实测：迁移写完后，另一个持有内存快照的 dsh 进程把文件重写回去。
// 这里用「迁移函数内部会 sleep 600ms 再复读」这一点，起个 goroutine 在中间抢写。
func TestMigrateDetectsOverwriteAndRollsBack(t *testing.T) {
	creds := setupTemp(t)

	// 在迁移函数 sleep 的窗口里把真 key 写回去，模拟别的 dsh 进程
	done := make(chan struct{})
	go func() {
		defer close(done)
		for i := 0; i < 200; i++ {           // 最多等 2s
			time.Sleep(10 * time.Millisecond)
			cur, err := os.ReadFile(creds)
			if err != nil {
				continue
			}
			if strings.Contains(string(cur), refsPlaceholder) {
				// 已经被改成占位符了 —— 立刻写回真 key
				_ = os.WriteFile(creds, []byte(sampleCreds), 0o600)
				return
			}
		}
	}()

	_, err := migrateKeyToStore()
	<-done
	if err == nil {
		t.Fatal("被覆盖时必须报错，不能假装迁移成功")
	}
	if !strings.Contains(err.Error(), "覆盖") {
		t.Fatalf("错误信息应点明被覆盖，实际: %v", err)
	}
	after, _ := os.ReadFile(creds)
	if !strings.Contains(string(after), "sk-realkey-should-be-moved") {
		t.Fatal("回滚后真 key 必须还在文件里")
	}
	if loadStoredKey() != "" {
		t.Fatal("回滚后密钥库必须被清空")
	}
}

func TestStillRedacted(t *testing.T) {
	if !stillRedacted(sampleCreds[:0] + "version: 1\nrefs:\n  DEEPSEEK_API_KEY: broker-placeholder-x\n") {
		t.Fatal("占位符应算安全")
	}
	if stillRedacted(sampleCreds) {
		t.Fatal("真 key 不该算安全")
	}
	if !stillRedacted("version: 1\nrefs: {}\n") {
		t.Fatal("没有 refs 也算安全（没有可偷的东西）")
	}
}
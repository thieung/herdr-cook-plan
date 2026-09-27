# Herdr Cook Plan

[English](README.md) | Tiếng Việt

![Herdr Cook Plan: một coordinator, mỗi phase một pane worker mới, ledger và context guard](assets/cover.webp)

Skill cho coding agent, chạy một [AgentKit](https://agentkit.best/?ref=OMG49S8R) plan có sẵn qua
[Herdr](https://herdr.dev) theo từng phase. Mỗi phase có một worker agent mới trong pane Herdr riêng,
chạy tuần tự hoặc song song tùy dependency. Coordinator giám sát từ pane của mình: trả lời câu hỏi của
worker qua mailbox dạng file, xác minh bằng chứng của từng phase, commit rồi đóng những pane nó đã mở.
Coordinator không bao giờ tự implement một phase, nên nó chịu được compaction, hết quota hay đổi provider
mà không phải mang theo phần việc.

Skill phiên bản 1.0.0. Giấy phép MIT.

> **Bản công khai, cung cấp nguyên trạng.** Full support có ở phiên bản dành cho thành viên
> [nhóm Facebook Subscribers của Thieu Nguyen](https://www.facebook.com/groups/1312173340952529).
> Issue và pull request ở đây luôn được chào đón nhưng có thể không được phản hồi.

## Yêu cầu

- Herdr 0.9.1 trở lên, và skill phải được gọi **bên trong một pane Herdr** (`HERDR_ENV=1`).
- Integration của Herdr ở trạng thái `current` cho mọi runtime worker bạn dùng:
  `herdr integration status`, rồi `herdr integration install <kind>` cho kind nào còn thiếu.
- Engineer Kit của AgentKit trong mỗi runtime worker, vì worker chạy `ak:cook`:
  `ak kit init engineer --target <runtime> --yes`
- Python 3 (cho bước kiểm tra đóng run). Rất nên dùng Git: mỗi phase được nghiệm thu thành một commit.

## Cài đặt

Clone repository rồi copy thư mục `herdr-cook-plan/` vào thư mục skills của runtime:

```bash
git clone --depth 1 https://github.com/thieung/herdr-cook-plan.git
cp -R herdr-cook-plan/herdr-cook-plan ~/.claude/skills/
```

| Runtime | Theo dự án | Global |
|---|---|---|
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Codex | `.agents/skills/` | `~/.agents/skills/` |
| OMP | `.omp/skills/` | `~/.omp/agent/skills/` |
| Pi | `.pi/skills/` | `~/.pi/agent/skills/` |
| Cursor | `.cursor/skills/` | `~/.cursor/skills/` |
| Grok CLI | `.grok/skills/` | `~/.grok/skills/` |

Muốn cập nhật thì pull rồi copy lại.

### OMP: khởi động coordinator kèm context guard

Guard là một extension của OMP. Không có gì tự đăng ký nó, nên hãy khởi động coordinator kèm guard:

```bash
omp --extension ~/.omp/agent/skills/herdr-cook-plan/extensions/context-guard.mjs
```

Coordinator tự thêm cờ này cho mọi worker OMP nó khởi động. Đừng dùng `--no-extensions`: cờ đó tắt
luôn integration OMP của Herdr, khiến trạng thái `blocked`/`idle` của worker không còn đáng tin.

## Sử dụng

Mở một pane Herdr trong dự án rồi gọi skill với đường dẫn plan (hoặc thư mục chứa `plan.md`):

```text
/herdr-cook-plan plan.md --auto                          # Claude Code, Cursor, Grok CLI
/skill:herdr-cook-plan plan.md --auto                    # OMP, Pi
$herdr-cook-plan plan.md --select-agent --auto --advice  # Codex
/skill:herdr-cook-plan plan.md --runtime omp --omp-advisor all --auto
```

| Flag | Tác dụng |
|---|---|
| `--runtime <kind>` | Runtime của worker theo agent kind của Herdr. Mặc định là kind của chính coordinator. |
| `--model <id>` | Model chính của worker, truyền qua argv gốc của kind. |
| `--select-agent` | Hỏi runtime và model của worker một lần trước dispatch đầu tiên. |
| `--auto` | Coordinator tự quyết các approval trong phạm vi plan và ghi lý do. Không bao giờ bao gồm mở rộng scope, publish, deploy hay thao tác phá hủy. |
| `--omp-advisor all\|none\|<phase,...>` | Advisor riêng của OMP cho từng worker. Chỉ dùng cho worker OMP. |
| `--parallel` | Ưu tiên chạy song song; dependency và xung đột ghi vẫn buộc chạy tuần tự. |
| `--advice`, `--tdd`, các flag cook khác | Chuyển xuống `ak:cook` sau khi đối chiếu với bản cook đã cài. |

## Một run diễn ra thế nào

1. **Kiểm cổng.** Đang ở trong Herdr, Herdr 0.9.1 trở lên, integration đều `current`. Thiếu cổng nào là
   dừng run.
2. **Bảng đợt thực hiện.** Phase, dependency, phạm vi ghi, runtime và cách kiểm tra; phase độc lập chạy
   song song, mặc định hai worker.
3. **Dispatch.** Mỗi attempt một pane mới và một agent mới (`p02a01`, rồi `p02a02`), nhận prompt từ một
   file brief đã kiểm tra.
4. **Giám sát.** Mỗi vòng quét mailbox và chờ từng worker. Terminal idle không phải là thành công; hết
   giờ chờ không phải là đồng ý.
5. **Nghiệm thu và commit.** Phase được nghiệm thu khi có report `status: complete`, agent đã settle, diff
   đúng phạm vi và check pass. Ý định được ghi vào checkpoint trước, rồi commit chỉ các path của phase đó,
   rồi thêm dòng `phase-accepted` kèm SHA vào ledger. Pane của worker được đóng trước lần dispatch kế tiếp.
6. **Đóng run.** `implementation-summary.md`, `scripts/check-run-closed.py`, `run-completed`, và nhả
   lease sau cùng.

Trạng thái run nằm trong file dưới `<plan-dir>/reports/herdr-cook-runs/<runId>/`, nên coordinator bị
compaction hay restart vẫn khôi phục được ngay trong session đó. Toàn bộ vòng chạy, các file trạng thái
và bảng lỗi nằm trong [docs/workflow.md](docs/workflow.md) (tiếng Anh).

## Tình trạng và giới hạn đã biết

- Nhắc khôi phục tự động (context guard) chỉ có trên OMP. Runtime khác khôi phục từ các file checkpoint.
- Guard đã có unit test; hành vi của nó dưới một lần compaction thật chưa được quan sát trên 1.0.0.
- Live smoke đến nay còn giới hạn: hai phase được nghiệm thu và commit, bước kiểm tra đóng run pass.
- Worker vẫn có thể ghi thêm `plans/journals/*` ngoài phạm vi ghi của phase.
- Khả năng chạy cook trên Claude Code và Codex phải được dò trên máy bạn trước lần dispatch đầu.

Chạy test:

```bash
python3 herdr-cook-plan/tests/instruction-contract.test.py
node --test herdr-cook-plan/tests/context-guard.test.mjs
```

## Dùng Orca thay vì Herdr?

[Orca Cook Plan](https://cookplan.slopengineer.dev) là phiên bản Orca của workflow này, dành cho thành
viên nhóm Facebook Subscribers của Thieu Nguyen. Cài và cập nhật bằng một lệnh, tự đăng ký context guard
cho OMP. [Tham gia nhóm](https://www.facebook.com/groups/1312173340952529) để được cấp quyền.

## Giấy phép

MIT. Xem [LICENSE](LICENSE). Herdr và AgentKit là các dự án riêng với điều khoản riêng.

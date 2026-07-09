# BÁO CÁO ĐÁNH GIÁ & KẾ HOẠCH CẢI THIỆN WEBUI

## Tổng Quan

Frontend MekongAI Nexus sử dụng React 19 + Vite 6 + TypeScript 5.7 + Zustand 5 + Tailwind 3.4. Đây là một ứng dụng production-grade với kiến trúc mature phục vụ AI agent orchestration (SSE, WebSocket, polling). Ước tính ~8,000-10,000 LOC.

---

## I. ĐÁNH GIÁ HIỆN TẠI

### A. Điểm Mạnh (Giữ nguyên)

1. **Kiến trúc phân tách tốt**: API layer, stores, hooks, components tách biệt rõ ràng
2. **Real-time resilience**: Dual WebSocket + SSE streaming + DB-first polling fallback + missed event replay + F5 recovery
3. **Code splitting tốt**: 17 lazy-loaded routes với route-specific loading skeletons
4. **Type safety**: Strict TypeScript, comprehensive type definitions
5. **State management**: Zustand clean patterns — selectors, batch updates, throttled flush, LRU eviction (MAX_CACHED_SESSIONS=20)
6. **Performance cơ bản**: Virtual list (@tanstack/react-virtual), adaptive throttling, batched store updates
7. **Accessibility cơ bản**: SkipLink, VisuallyHidden, useFocusTrap, useAnnounce, ARIA live regions
8. **Theme system**: CSS variables + 4 presets + custom accent color generation
9. **Plugin system**: Slot-based UI injection + dynamic route registration
10. **i18n**: Type-safe Vietnamese + English
11. **Error handling multi-layer**: AppErrorBoundary, route ErrorBoundary, PluginErrorBoundary, toast notifications
12. **Auth mature**: JWT refresh dedup, cross-tab sync, proactive refresh scheduling
13. **Design system documented**: DESIGN.md chi tiết với typography, elevation, spacing

### B. Vấn Đề Cần Cải Thiện

#### B1. Kiến Trúc & Cấu Trúc Code (Critical)

| # | Vấn đề | Mức độ | Chi tiết |
|---|--------|--------|----------|
| 1 | App.tsx quá lớn (659 lines) | 🔴 High | Chứa navigation, routing, auth guard, keyboard shortcuts, system health check, global components — tất cả trong 1 file |
| 2 | stream.ts quá lớn (1259 lines) | 🔴 High | Switch statement khổng lồ với ~50 event case — khó maintain, khó test |
| 3 | processStore.ts (1017 lines) | 🟡 Medium | Store complexity quá cao, nên tách thành composable slices |
| 4 | Thiếu barrel exports | 🟡 Medium | hooks/ không có index.ts, imports không nhất quán |

#### B2. Styling & Design System (High Impact)

| # | Vấn đề | Mức độ | Chi tiết |
|---|--------|--------|----------|
| 5 | Hardcoded colors | 🔴 High | App.tsx dùng inline `dark:bg-[#0f0f17]`, `dark:text-[#a0a0b8]` thay vì CSS variables |
| 6 | Mixed styling approaches | 🔴 High | CSS variables (index.css) + Tailwind utilities + inline hex colors — KHÔNG nhất quán |
| 7 | Design tokens chưa centralized | 🟡 Medium | Tailwind config define colors nhưng nhiều component bypass dùng hardcoded values |
| 8 | Responsive chưa đầy đủ | 🟡 Medium | DESIGN.md define responsive breakpoints nhưng implementation chưa cover hết |

#### B3. Performance (Medium)

| # | Vấn đề | Mức độ | Chi tiết |
|---|--------|--------|----------|
| 9 | executionStore O(n) scan | 🟡 Medium | `applyEventToList` scan toàn bộ existing events mỗi lần thêm — nên dùng Map/Set |
| 10 | Thiếu React.memo | 🟡 Medium | Navigation items, sidebar items — re-render không cần thiết |
| 11 | VirtualMessageList đơn giản | 🟡 Medium | Chưa handle dynamic height well — message lengths vary wildly |
| 12 | Adaptive flush lag | 🟢 Low | HIGH_FLUSH_MS=400ms users có thể notice delay trên execution timeline |

#### B4. Accessibility (Medium)

| # | Vấn đề | Mức độ | Chi tiết |
|---|--------|--------|----------|
| 13 | More dropdown thiếu ARIA | 🟡 Medium | Không có `aria-expanded`, `aria-haspopup` |
| 14 | Mobile menu thiếu roles | 🟡 Medium | Thiếu `role="menu"`, `aria-label` |
| 15 | Navigation thiếu aria-current | 🟡 Medium | Active items thiếu `aria-current="page"` |
| 16 | Color contrast chưa verified | 🟡 Medium | Purple-tinted shadows + light text có thể fail WCAG AA |

#### B5. Code Quality (Low-Medium)

| # | Vấn đề | Mức độ | Chi tiết |
|---|--------|--------|----------|
| 17 | `any` types bypass | 🟡 Medium | Nhiều `(event as any)` trong stream.ts |
| 18 | localStorage fragmentation | 🟡 Medium | Stores dùng trực tiếp localStorage lẫn với `src/lib/storage.ts` |
| 19 | eslint-disable scattered | 🟢 Low | Nhiều `eslint-disable-next-line react-hooks/exhaustive-deps` — potential stale closures |
| 20 | Magic strings | 🟢 Low | Event types scattered — nên dùng const enum |
| 21 | window pollution | 🟢 Low | `(window as any).__tokenRefreshTimer` — nên dùng module variable |
| 22 | No unit tests | 🟡 Medium | Vitest configured nhưng không có test files |

#### B6. UX Improvements Cần Thiết

| # | Vấn đề | Mức độ | Chi tiết |
|---|--------|--------|----------|
| 23 | Loading states chưa polish | 🟡 Medium | PageSkeletons tốt nhưng inline loading indicators không consistent |
| 24 | Error states chưa friendly | 🟡 Medium | Technical errors hiển thị trực tiếp — cần human-friendly messages |
| 25 | Empty states chưa đủ | 🟢 Low | EmptyState component có nhưng chưa dùng ở tất cả nơi cần |
| 26 | Animations chưa tối ưu | 🟢 Low | Nhiều animations defined nhưng `prefers-reduced-motion` chưa respect đầy đủ |
| 27 | Mobile experience | 🟡 Medium | Desktop-first, mobile UX cần review |

---

## II. KẾ HOẠCH CẢI THIỆN

### Phase 1: Refactoring Cấu Trúc (Ưu tiên cao nhất)

**Mục tiêu**: Giảm file size, tăng maintainability, không thay đổi behavior

#### 1.1 Tách App.tsx (~659 lines → 5-6 files nhỏ)

- `src/router/routes.tsx` — Route definitions + lazy imports
- `src/router/AppRouter.tsx` — Router component + Suspense
- `src/router/AuthGuard.tsx` — Authentication redirect logic
- `src/components/layout/Navigation.tsx` — Sidebar navigation
- `src/components/layout/GlobalProviders.tsx` — Top-level providers + global components
- `src/hooks/useSystemHealth.ts` — System health polling logic
- `src/hooks/useGlobalShortcuts.ts` — Keyboard shortcuts

#### 1.2 Tách stream.ts (~1259 lines → modular handlers)

- `src/api/stream/index.ts` — Main orchestrator
- `src/api/stream/types.ts` — Event types enum + interfaces
- `src/api/stream/connection.ts` — SSE connection management + retry
- `src/api/stream/handlers/chat.ts` — Chat event handlers
- `src/api/stream/handlers/process.ts` — Process/orchestration handlers
- `src/api/stream/handlers/execution.ts` — Execution event handlers
- `src/api/stream/handlers/project.ts` — Project builder handlers

#### 1.3 Tách processStore.ts (~1017 lines)

- `src/stores/process/types.ts` — Type definitions
- `src/stores/process/actions.ts` — Action creators
- `src/stores/process/selectors.ts` — Memoized selectors
- `src/stores/process/index.ts` — Main store (slim)

### Phase 2: Styling Consistency (High Impact UX)

**Mục tiêu**: Thống nhất design system, loại bỏ hardcoded values

#### 2.1 Design Token Migration

- Audit tất cả hardcoded hex colors → map sang Tailwind/CSS variable
- Tạo `src/styles/tokens.ts` export design tokens for JS usage
- Replace inline `dark:bg-[#xxx]` với semantic classes

#### 2.2 Component Style Audit

- Đảm bảo mọi component dùng Tailwind classes reference CSS variables
- Tạo consistent spacing scale (không mix px/rem/tailwind)
- Kiểm tra dark mode coverage trên tất cả components

#### 2.3 Responsive Polish

- Review mobile breakpoints cho tất cả pages
- Test navigation collapse behavior
- Đảm bảo touch targets ≥ 44px trên mobile

### Phase 3: Performance Optimization

**Mục tiêu**: Giảm re-renders, tối ưu memory

#### 3.1 Memoization

- Wrap navigation items trong `React.memo`
- Add `useMemo` cho computed values trong stores
- Implement `useShallow` selector cho Zustand stores nơi cần

#### 3.2 executionStore Optimization

- Replace linear scan với Map-based deduplication
- Pre-index events by `eventId` + `sequence`

#### 3.3 VirtualMessageList Enhancement

- Dynamic height estimation
- Smooth scroll-to-bottom behavior
- Sticky date separators

### Phase 4: Accessibility & UX Polish

**Mục tiêu**: WCAG AA compliance

#### 4.1 ARIA Fixes

- Add `aria-expanded`, `aria-haspopup` cho dropdowns
- Add `role="menu"`, `role="menuitem"` cho navigation menus
- Add `aria-current="page"` cho active nav items
- Verify focus management in modals

#### 4.2 Color Contrast Audit

- Run WCAG contrast checker trên tất cả text/background combinations
- Fix any failing combinations (đặc biệt purple shadows + light text)

#### 4.3 Motion & Animation

- Add `@media (prefers-reduced-motion: reduce)` cho tất cả animations
- Ensure no content depends solely on animation to be understood

#### 4.4 UX Improvements

- Consistent loading indicators across all async operations
- Human-friendly error messages (map technical errors → user messages)
- EmptyState component usage ở mọi list/grid khi empty
- Mobile navigation optimization

### Phase 5: Code Quality & Testing

**Mục tiêu**: Type safety, test coverage, maintainability

#### 5.1 Type Safety

- Replace `any` casts trong stream.ts với proper discriminated unions
- Create event type map: `Record<EventType, EventPayload>`
- Add runtime validation cho API responses (zod hoặc manual guards)

#### 5.2 Barrel Exports

- Add `src/hooks/index.ts`
- Add `src/stores/index.ts`
- Standardize import paths

#### 5.3 localStorage Consolidation

- Migrate ALL localStorage usage qua `src/lib/storage.ts`
- Remove direct localStorage calls trong stores

#### 5.4 Unit Tests (Minimum Coverage)

- Test critical hooks: useResilientChat, useTaskPolling
- Test store logic: sessionStore, processStore
- Test utilities: formatDate, generateId, debounce

---

## III. PRIORITY MATRIX

| Phase | Impact | Effort | Timeline |
|-------|--------|--------|----------|
| Phase 1: Refactoring | 🔴 High (maintainability) | Medium (2-3 days) | Tuần 1 |
| Phase 2: Styling | 🔴 High (UX) | Medium (2 days) | Tuần 1-2 |
| Phase 3: Performance | 🟡 Medium | Low-Medium (1-2 days) | Tuần 2 |
| Phase 4: Accessibility | 🟡 Medium | Low (1 day) | Tuần 2 |
| Phase 5: Code Quality | 🟢 Low-Medium | High (3-4 days) | Tuần 3 |

---

## IV. KẾT LUẬN

Frontend MekongAI Nexus đã ở mức **tốt** cho một dự án AI agent platform phức tạp. Tech stack hiện đại, patterns đúng chuẩn. Vấn đề chính là:

1. **Một số file quá lớn** — cần refactor thành modules nhỏ hơn
2. **Styling không nhất quán** — hardcoded colors vs design system
3. **Thiếu tests** — rủi ro regression khi refactor

**Khuyến nghị**: Bắt đầu từ Phase 1 (Refactoring) vì nó tạo nền tảng cho tất cả cải thiện khác mà không ảnh hưởng đến user-facing behavior.

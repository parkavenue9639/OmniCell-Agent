# Frontend 边界

本目录承载面向 conversation 的 Web 产品界面。Frontend 只消费版本化公共契约和类型化事件，不依赖 backend 内部状态、数据库结构或工作流节点。

## 状态与传输

- REST 负责 conversation、run、review、artifact 等资源查询和命令。
- fetch-based SSE 只使用 `Last-Event-ID` 续传持久化事件；页面断线不会取消 run。
- 事件必须先通过 `contracts/events/v1.schema.json` 运行时校验，再进入纯 projector。
- sequence 始终按十进制字符串和 `BigInt` 比较，不转换为 JavaScript `Number`。
- Backend 尚未生产的 `assistant.delta` 与 `capability.progress` 不在界面中伪造。

## 本地开发

```bash
npm ci --cache /private/tmp/omnicell-npm-cache
npm run dev
```

开发服务器默认将 `/api/v1` 代理到 `http://127.0.0.1:8000`。需要修改时设置 `OMNICELL_API_PROXY_TARGET`。

## 验证

```bash
npm run contracts:check
npm run typecheck
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

`contracts:generate` 只从仓库根目录的冻结契约生成 TypeScript；`contracts:check` 在临时目录重建并按字节比较，不会覆盖工作区。

Playwright 默认使用自己隔离管理的 Chromium，避免测试进程触发或污染日常使用的系统 Chrome。只有明确需要验证系统浏览器 channel 时，才设置 `OMNICELL_PLAYWRIGHT_BROWSER_CHANNEL=chrome`。

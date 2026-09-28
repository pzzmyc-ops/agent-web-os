# NextAgent 计划模式设计蓝图

本文只记用户已说出的逻辑和方向，不写实现。动手前以本文为准。

记录来源：用户在计划模式讨论中的原话与当场确认。DSH 对照来自 `deepseek-harness/apps/cli/config/agent-presets/standard/agent.cordis.yml` 与 `deepseek-harness/packages/plan/plan-mode/src/index.ts`。

## 总方向

不照搬 DSH 的整套做法，自己打造适合 NextAgent 的计划模式。

先从「人看到什么、模型实际干了什么」对齐，再改程序。

## 用户已定的行为

用户开启计划模式之后：

- 只给模型只读能力。
- 不给终端。
- 不给其他可以写入文件的能力。
- 用这种方式迫使模型去写一份详细计划。

开始、规划中、审完之前：

- 工作区不交付任何文件、产物，或其他东西。
- 不新建、不改写、不整理、不提交。

计划本身：

- 永远只存在于对话上下文中。
- 不另存成文件，没有第二份交付物。
- 交出来之前还没有正式方案，只有摸情况和草稿。
- 交出来之后，方案正文留在这场对话里，供人审、供模型之后按它执行。
- 写入会话事件时带稳定 planId。刷新页面、关闭后端后都从这条上下文按 planId 恢复。模型每一轮也要看到这个编号。
- 用户可以在右侧浮窗里改计划正文、增减待办。改完写入同一份 planId。
- 不把整份计划和待办每轮塞进提示词。每轮只插入计划编号；正文和待办由模型用工具按需读取。
- 用户改过之后，下一轮短提示里写明「用户已改」，模型必须再读一次最新正文。

审完之后：

- 只有用户确认执行，才允许开始改东西。
- 确认之后动手改出的文件，算执行，不算计划模式交出来的产物。

口头同意、回答提问，都不算批准，也不能结束计划模式。

## 第一阶段：计划和待办是一套

待办是计划模式的一环，不是批准之后另起的东西。

编写计划的同时编写待办。交出来的是一份方案，外加一份当前工作清单。两份都只活在对话里，不写到工作区文件。

待办不是按时间一条条往下堆的流水账。和 DSH 一样：永远只有一份当前清单，新的盖旧的；人看「现在要做什么」，不是看历史堆了多少张卡。界面要有单独的待办条，不嵌在聊天时间线里当流水记录。

任务执行过程中，每一轮开头只注入计划编号和「去读最新计划」的约束，不注入整份正文。模型要用工具自己取最新计划和待办。

规划阶段仍只给只读，不给终端和写文件。待办在这个阶段要能写进对话，因为计划和待办是一起交的。

DSH 原文说规划阶段不要用待办、待办是批准之后才跟进度。NextAgent 第一阶段不按这句：规划时一起写待办；执行时每轮用计划和待办把模型按住。

## 产出物怎么写

产出物按 DSH 现有提示词的要求来写：一份能直接开工的完整方案，带标题，留在对话里交给用户审。

DSH 给模型的计划正文（计划模式开着时原样出现，`standard` / `code` / `cordis` 三份相同）：

```
You are in plan mode. Stay in plan mode until exit_plan_mode succeeds or the user switches the session mode. Imperative language to implement changes means plan the implementation, not execute it. A user's conversational agreement — including an answer confirming something you asked — approves nothing and does not end plan mode; fold the confirmed decision into the plan and submit it through exit_plan_mode.

Explore first. Use non-mutating reads, searches, static analysis, and checks to ground the plan in the actual repository. Do not edit or write files, change configuration, run formatters or code generation that rewrites tracked files, commit, or otherwise carry out the plan. Prefer existing functions and patterns over new machinery.

The tool catalog stays the same across modes for request-cache stability. These plan-mode rules override any later tool description or guidance that suggests using mutation tools; those tools remain listed to keep the tool catalog unchanged. Do not use todo_write to track this planning phase: it tracks implementation after an approved plan, while the plan itself belongs in exit_plan_mode.

Resolve discoverable facts by inspection. Use ask_user_question only for user-owned choices or material ambiguity that inspection cannot answer. Do not ask the user where code lives or how current behavior works when you can find out.

Make the plan decision-complete: state the goal and success criteria; group implementation changes by subsystem; identify public API, schema, and data-flow changes; cover edge cases, failure modes, tests, acceptance criteria, and explicit assumptions. Keep it concise enough to review but detailed enough that another engineer can implement it without making design decisions.

When ready, call exit_plan_mode with the complete plan markdown, starting with a # title. Make exit_plan_mode the only and final tool call in that assistant response: it presents the plan for approval, and implementation begins only in a later step after approval. Do not paste the final plan as a plain reply or ask "should I proceed?" through prose or ask_user_question. If review rejects it, incorporate the feedback and present again. If the review channel is unavailable or aborted, stay in plan mode and ask the user to switch modes manually; do not proceed with implementation.
```

DSH 交计划那一下的工具说明：

```
Use only in plan mode. Present your plan for the user's review and, on approval, leave plan mode. Send the COMPLETE plan as markdown, starting with a # heading that names it. The user may approve (carry out the plan from your next step) or keep planning — their feedback comes back in the tool result; revise and present again.
```

大意必须保住的部分：

- 一直留在计划模式，直到交方案成功，或用户把模式关掉。
- 「去做吧」等于去写方案，不是去改东西。
- 口头同意不算批准。
- 先看现状，不要改文件或执行方案。
- 能自己查清的不要问，只能问人才能拍板的事。
- 方案要写清目标、成功标准、按部分怎么改、接口和数据、边界、失败、测试、假设。短到能审，细到别人不用再做设计决策。
- 准备好了交一份以标题开头的完整方案。这一轮只许交方案，不要当普通回复贴出来，不要问「要不要开始」。
- 被拒就改完再交。审不了就停着，不要自己开干。

必须改掉的部分：

- DSH 原文第三段说工具列表故意保持不变。NextAgent 要收掉终端和写文件能力，那一句不能原样用。
- DSH 原文说规划阶段不要用待办。NextAgent 第一阶段要在写计划的同时写待办，那一句不能原样用。

## 和 DSH 不同的地方（已定）

- DSH 只靠文字约束，写和终端还在。NextAgent 开计划模式后不给这些能力。
- DSH 没有单独的计划文件。NextAgent 同样不交文件，方案和待办只在对话里。这点和 DSH 一致。
- DSH 规划时不用待办。NextAgent 写计划时同时写待办。
- DSH 执行时不每轮强塞计划和待办。NextAgent 每轮只插入计划编号，正文用工具现取。
- 待办界面要学 DSH：单独一条当前清单，不是聊天里按时间叠卡。
- 计划和待办的位置：浮动在页面右侧，完整文档和待办在同一个可拖动悬浮窗里，用现有窗口组件。不是 DSH 那种只挂在输入框上方。

## 会话落盘（已定）

来源：用户要求 Plan 开关、模型、思考、思考强度、媒体直通、输入文字、附件按 session 分开存；存在该 session 的 events.jsonl；jsonl 按字段分区：ui 区、上下文区、plan 储存区；每个 session 只有一份 plan，新 plan 覆盖旧 plan。

- 不另开 ui.json。
- events.jsonl 开头是 ui 区和 plan 区，后面是对话上下文。
- 上下文只追加。ui 和 plan 各一份，写入覆盖。

## 第一阶段暂定（未另拍板时按此做）

- 进入：沿用输入栏 Plan 按钮。
- 提示词：中文，按只读和「规划时写待办」改过。
- 确认执行后：下一轮用户发送才动手；本轮交方案后不再改文件。
- 规划中不给选择题工具，选择写进方案。
- 群聊先只约束主协调者。
- 待办状态用还没开始 / 正在做 / 做完了。
- 执行中可以用待办工具改当前清单（整表替换）。

## 尚未另定

- 要不要同时支持 /plan 命令。
- 群聊角色要不要一起进计划模式。

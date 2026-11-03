"""悬浮窗（advisor/ui/overlay.py）的部件，各模块开头写着放了什么。

悬浮窗本身（Overlay）留在 overlay.py：窗口、刷新的编排和对外的方法；这里的部件由它组合起来用。分四类：
- 外壳：overlay.py、app.py（组装、启动、退出）、layout.py（搭界面），可以碰悬浮窗；
- 编排：dispatch.py（事件分派），调各部件的公开方法、写公开的显示状态对象；
- 部件：各张卡片、提示行、跟随和托盘、子窗口、热键、后台任务、皮肤解锁计数、识别线程。不拿着悬浮窗，构造时只收快照、
  服务、回调、自己管的控件（外壳搭好交进来）和只读的显示状态；部件之间只经外壳接好的回调来往，不互相导入；
- 底层模块：wording、geometry、factory、option_widgets、state，部件都可以导入。state.py 里的 SessionAPI 列着悬浮窗
  用到的 Session 接口。
这几条由 tests/test_overlay_guard.py 守着。模块顶层不导入 overlay.py（只有 app.build 建悬浮窗时在函数里导入，免得成环）。
advisor.ui.overlay 对外的名字从那里导入，不经本包转。
"""

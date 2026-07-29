"""Hello World 示例插件：启动时打印 Hello World。"""


class Plugin:
    def on_load(self, ctx):
        print("Hello World")
        try:
            ctx.logger.info("Hello World")
        except Exception:
            pass

    def on_unload(self, ctx):
        pass


def get_plugin():
    return Plugin()

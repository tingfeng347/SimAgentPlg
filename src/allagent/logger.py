import logging


def setup_logger(
    name: str = "All-Agent",
    level: str = "INFO",
    log_file: str = "./logs/app.log",
    fmt: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
):
    """创建并配置日志记录器，输出到控制台。

    Args:
        name: 日志记录器名称。
        level: 最低日志级别，默认为 "INFO"。
        log_file: 日志文件路径（当前仅保留参数，暂未启用文件输出）。
        fmt: 日志格式字符串。

    Returns:
        配置完成的 logging.Logger 实例。
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "All-Agent") -> logging.Logger:
    """获取或创建指定名称的日志记录器。

    Args:
        name: 日志记录器名称，默认为 "All-Agent"。

    Returns:
        配置完成的 logging.Logger 实例。
    """
    return setup_logger(name)

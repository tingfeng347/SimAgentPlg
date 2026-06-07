import logging

def setup_logger(
    name: str = "All-Agent",
    level: str = "INFO",
    log_file: str = "./logs/app.log",
    fmt: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
):

    """Setup logger"""
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
    return setup_logger(name)

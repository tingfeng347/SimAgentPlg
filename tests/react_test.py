from simagentplg import BaseAgent


async def main():
    task = "现在杭州天气如何？"
    loop = BaseAgent()
    result = await loop.runtime(task=task)
    print(f"\n===== 最终结果 =====\n{result}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())

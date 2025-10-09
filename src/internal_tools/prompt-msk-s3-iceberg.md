你是AWS大数据资深的架构师和专家,，你需要帮我做如下代码的重构

# 基础信息
1. 原始代码路径 /home/ec2-user/environment/clickstream-lakehouse/src/msk-iceberg/create-s3-iceberg-connector-optimized.sh 和/home/ec2-user/environment/clickstream-lakehouse/src/msk-iceberg/README.md 


# 你需要完成如下任务
1. 认真读取的所有文件，深入理解代码逻辑,之后将create-s3-iceberg-connector-optimized.sh逻辑转换为python的生产级别代码实现，注意函数参数和注释，必须参数和可选参数按照当前代码逻辑实现
2. 需要创建boot3 session时，可以使用当前目录下aws_session.py提供的方法，你可以读取改文件内容，学习如何使用。
3. 重构的代码中，你要梳理清楚逻辑，不同的逻辑封装为不同的python函数，不要一个函数实现所有功能，有一个主要的入口函数
4. 做一个__main__的测试的方式，能够运行测试，这个只是用来测试，我后面使用的时候会在其它文件调用主要入海口函数使用
5. 日志的打印要用生产级别的logging ，不要用print
6. 忽略README文件和s3-json-connector-optimized 相关内容，你不需要考虑这部分和s3-json-connector相关的

# 验证
1. 执行你重构后的代码，确保可以正常执行
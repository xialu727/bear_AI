import pygame

import random



# 初始化pygame

pygame.init()



# 屏幕大小

width, height = 600, 400

screen = pygame.display.set_mode((width, height))

pygame.display.set_caption("贪吃蛇小游戏")



# 颜色定义

white = (255, 255, 255)

black = (0, 0, 0)

red = (213, 50, 80)

green = (0, 255, 0)



# 蛇和食物的尺寸

snake_block = 10

snake_speed = 15



# 字体设置

font_style = pygame.font.SysFont("bahnschrift", 25)

score_font = pygame.font.SysFont("comicsansms", 35)



# 显示得分

def your_score(score):

    value = score_font.render("Score: " + str(score), True, white)

    screen.blit(value, [0, 0])



# 绘制蛇

def our_snake(snake_block, snake_list):

    for x in snake_list:

        pygame.draw.rect(screen, green, [x[0], x[1], snake_block, snake_block])



# 消息显示

def message(msg, color):

    mesg = font_style.render(msg, True, color)

    screen.blit(mesg, [width / 6, height / 3])



# 游戏主循环

def gameLoop():

    game_over = False

    game_close = False



    # 初始位置

    x1 = width / 2

    y1 = height / 2



    # 移动方向

    x1_change = 0

    y1_change = 0



    

    snake_list = []

    length_of_snake = 1



    # 食物位置

    foodx = round(random.randrange(0, width - snake_block) / 10.0) * 10.0

    foody = round(random.randrange(0, height - snake_block) / 10.0) * 10.0



    clock = pygame.time.Clock()



    while not game_over:



        while game_close:

            screen.fill(black)

            message("你输了! 按 Q 退出或 C 重新开始", red)

            your_score(length_of_snake - 1)

            pygame.display.update()



            for event in pygame.event.get():

                if event.type == pygame.KEYDOWN:

                    if event.key == pygame.K_q:

                        game_over = True

                        game_close = False

                    if event.key == pygame.K_c:

                        gameLoop()



        for event in pygame.event.get():

            if event.type == pygame.QUIT:

                game_over = True

            if event.type == pygame.KEYDOWN:

                if event.key == pygame.K_LEFT:

                    x1_change = -snake_block

                    y1_change = 0

                elif event.key == pygame.K_RIGHT:

                    x1_change = snake_block

                    y1_change = 0

                elif event.key == pygame.K_UP:

                    y1_change = -snake_block

                    x1_change = 0

                elif event.key == pygame.K_DOWN:

                    y1_change = snake_block

                    x1_change = 0



        # 检查是否撞墙

        if x1 >= width or x1 < 0 or y1 >= height or y1 < 0:

            game_close = True



        x1 += x1_change

        y1 += y1_change

        screen.fill(black)

        pygame.draw.rect(screen, red, [foodx, foody, snake_block, snake_block])



        # 蛇头

        snake_head = [x1, y1]

        snake_list.append(snake_head)



        # 如果蛇身超过长度，删除最前面的块

        if len(snake_list) > length_of_snake:

            del snake_list[0]



        # 检查是否撞到自己

        for x in snake_list[:-1]:

            if x == snake_head:

                game_close = True



        our_snake(snake_block, snake_list)

        your_score(length_of_snake - 1)



        pygame.display.update()



        # 吃到食物

        if x1 == foodx and y1 == foody:

            foodx = round(random.randrange(0, width - snake_block) / 10.0) * 10.0

            foody = round(random.randrange(0, height - snake_block) / 10.0) * 10.0

            length_of_snake += 1



        clock.tick(snake_speed)



    pygame.quit()

    quit()



# 启动游戏

gameLoop()


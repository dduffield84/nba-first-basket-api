-- Same tables db.py creates on Postgres, for testing scripts/db/*.sh.
create table first_baskets (game_id varchar(32) primary key, game_date varchar(10) not null, player_id varchar(32) not null, player_name varchar(100) not null, team varchar(3) not null, source varchar(200) not null, verified integer not null, recorded_at varchar(40) not null, season varchar(7), home_team varchar(3), away_team varchar(3), tip_winner_team varchar(3), data_flags text);
create table game_starters (game_id varchar(32), player_id varchar(32), player_name varchar(100) not null, team varchar(3) not null, game_date varchar(10) not null, primary key (game_id, player_id));
create table prediction_sets (id serial primary key, game_id varchar(32) not null, game_date varchar(10) not null, model varchar(100) not null, created_at varchar(40) not null, tip_time varchar(40), home_team varchar(3), away_team varchar(3));
create table prediction_items (set_id integer references prediction_sets(id), player_id varchar(32), player_name varchar(100) not null, team varchar(3) not null, probability float not null, primary key (set_id, player_id));
-- Synthetic prediction rows (test fixtures only, not real predictions).
insert into prediction_sets(game_id, game_date, model, created_at)
  select 'test' || i, '2026-11-01', 'test_fixture', '2026-11-01T00:00:00Z'
  from generate_series(1, 40) i;
insert into prediction_items
  select s.id, 'p' || j, 'Test Player ' || j, 'TST', 0.1
  from prediction_sets s, generate_series(1, 10) j;
